"""
If you use this code, please cite one of the SynthSeg papers:
https://github.com/BBillot/SynthSeg/blob/master/bibtex.bib

Copyright 2020 Benjamin Billot

Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in
compliance with the License. You may obtain a copy of the License at
https://www.apache.org/licenses/LICENSE-2.0
Unless required by applicable law or agreed to in writing, software distributed under the License is
distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
implied. See the License for the specific language governing permissions and limitations under the
License.
"""


# python imports
import numpy as np
import tensorflow as tf
import keras.layers as KL
from keras.models import Model

# third-party imports
from ext.lab2im import utils
from ext.lab2im import layers
from ext.lab2im import edit_tensors as l2i_et
from ext.lab2im.edit_volumes import get_ras_axes


def labels_to_image_model(labels_shape,
                          n_channels,
                          generation_labels,
                          output_labels,
                          n_neutral_labels,
                          atlas_res,
                          target_res,
                          output_shape=None,
                          output_div_by_n=None,
                          flipping=True,
                          aff=None,
                          scaling_bounds=0.2,
                          rotation_bounds=15,
                          shearing_bounds=0.012,
                          translation_bounds=False,
                          nonlin_std=3.,
                          nonlin_scale=.0625,
                          randomise_res=False,
                          max_res_iso=4.,
                          max_res_aniso=8.,
                          data_res=None,
                          thickness=None,
                          bias_field_std=.5,
                          bias_scale=.025,
                          return_gradients=False):
    """
    This function builds a keras/tensorflow model to generate images from provided label maps.
    The images are generated by sampling a Gaussian Mixture Model (of given parameters), conditioned on the label map.
    The model will take as inputs:
        -a label map
        -a vector containing the means of the Gaussian Mixture Model for each label,
        -a vector containing the standard deviations of the Gaussian Mixture Model for each label,
    The model returns:
        -the generated image normalised between 0 and 1.
        -the corresponding label map, with only the labels present in output_labels (the other are reset to zero).
    # IMPORTANT !!!
    # Each time we provide a parameter with separate values for each axis (e.g. with a numpy array or a sequence),
    # these values refer to the RAS axes.
    :param labels_shape: shape of the input label maps. Can be a sequence or a 1d numpy array.
    :param n_channels: number of channels to be synthesised.
    :param generation_labels: (optional) list of all possible label values in the input label maps.
    Default is None, where the label values are directly gotten from the provided label maps.
    If not None, can be a sequence or a 1d numpy array. It should be organised as follows: background label first, then
    non-sided labels (e.g. CSF, brainstem, etc.), then all the structures of the same hemisphere (can be left or right),
    and finally all the corresponding contralateral structures (in the same order).
    :param output_labels: (optional) list of the same length as generation_labels to indicate which values to use in the
    label maps returned by this model, i.e. all occurrences of generation_labels[i] in the input label maps will be
    converted to output_labels[i] in the returned label maps. Examples:
    Set output_labels[i] to zero if you wish to erase the value generation_labels[i] from the returned label maps.
    Set output_labels[i]=generation_labels[i] to keep the value generation_labels[i] in the returned maps.
    Can be a list or a 1d numpy array. By default output_labels is equal to generation_labels.
    :param n_neutral_labels: number of non-sided generation labels.
    :param atlas_res: resolution of the input label maps.
    Can be a number (isotropic resolution), a sequence, or a 1d numpy array.
    :param target_res: target resolution of the generated images and corresponding label maps.
    Can be a number (isotropic resolution), a sequence, or a 1d numpy array.
    :param output_shape: (optional) desired shape of the output image, obtained by randomly cropping the generated image
    Can be an integer (same size in all dimensions), a sequence, a 1d numpy array, or the path to a 1d numpy array.
    Default is None, where no cropping is performed.
    :param output_div_by_n: (optional) forces the output shape to be divisible by this value. It overwrites output_shape
    if necessary. Can be an integer (same size in all dimensions), a sequence, or a 1d numpy array.
    :param flipping: (optional) whether to introduce right/left random flipping
    :param aff: (optional) example of an (n_dims+1)x(n_dims+1) affine matrix of one of the input label map.
    Used to find brain's right/left axis. Should be given if flipping is True.
    :param scaling_bounds: (optional) range of the random scaling to apply at each mini-batch. The scaling factor for
    each dimension is sampled from a uniform distribution of predefined bounds. Can either be:
    1) a number, in which case the scaling factor is independently sampled from the uniform distribution of bounds
    [1-scaling_bounds, 1+scaling_bounds] for each dimension.
    2) a sequence, in which case the scaling factor is sampled from the uniform distribution of bounds
    (1-scaling_bounds[i], 1+scaling_bounds[i]) for the i-th dimension.
    3) a numpy array of shape (2, n_dims), in which case the scaling factor is sampled from the uniform distribution
     of bounds (scaling_bounds[0, i], scaling_bounds[1, i]) for the i-th dimension.
    4) False, in which case scaling is completely turned off.
    Default is scaling_bounds = 0.2 (case 1)
    :param rotation_bounds: (optional) same as scaling bounds but for the rotation angle, except that for cases 1
    and 2, the bounds are centred on 0 rather than 1, i.e. [0+rotation_bounds[i], 0-rotation_bounds[i]].
    Default is rotation_bounds = 15.
    :param shearing_bounds: (optional) same as scaling bounds. Default is shearing_bounds = 0.012.
    :param translation_bounds: (optional) same as scaling bounds. Default is translation_bounds = False, but we
    encourage using it when cropping is deactivated (i.e. when output_shape=None in BrainGenerator).
    :param nonlin_std: (optional) Maximum value for the standard deviation of the normal distribution from which we
    sample the first tensor for synthesising the deformation field. Set to 0 if you wish to completely turn the elastic
    deformation off.
    :param nonlin_scale: (optional) if nonlin_std is strictly positive, factor between the shapes of the input
    label maps and the shape of the input non-linear tensor.
    :param randomise_res: (optional) whether to mimic images that would have been 1) acquired at low resolution, and
    2) resampled to high resolution. The low resolution is uniformly resampled at each minibatch from [1mm, 9mm].
    In that process, the images generated by sampling the GMM are 1) blurred at the sampled LR, 2) downsampled at LR,
    and 3) resampled at target_resolution.
    :param max_res_iso: (optional) If randomise_res is True, this enables to control the upper bound of the uniform
    distribution from which we sample the random resolution U(min_res, max_res_iso), where min_res is the resolution of
    the input label maps. Must be a number, and default is 4. Set to None to deactivate it, but if randomise_res is
    True, at least one of max_res_iso or max_res_aniso must be given.
    :param max_res_aniso: If randomise_res is True, this enables to downsample the input volumes to a random LR in
    only 1 (random) direction. This is done by randomly selecting a direction i in the range [0, n_dims-1], and sampling
    a value in the corresponding uniform distribution U(min_res[i], max_res_aniso[i]), where min_res is the resolution
    of the input label maps. Can be a number, a sequence, or a 1d numpy array. Set to None to deactivate it, but if
    randomise_res is True, at least one of max_res_iso or max_res_aniso must be given.
    :param data_res: (optional) specific acquisition resolution to mimic, as opposed to random resolution sampled when
    randomise_res is True. This triggers a blurring to mimic the specified acquisition resolution, but the downsampling
    is optional (see param downsample). Default for data_res is None, where images are slightly blurred.
    If the generated images are uni-modal, data_res can be a number (isotropic acquisition resolution), a sequence, a 1d
    numpy array, or the path to a 1d numpy array. In the multi-modal case, it should be given as a numpy array (or a
    path) of size (n_mod, n_dims), where each row is the acquisition resolution of the corresponding channel.
    :param thickness: (optional) if data_res is provided, we can further specify the slice thickness of the low
    resolution images to mimic. Must be provided in the same format as data_res. Default thickness = data_res.
    :param bias_field_std: (optional) If strictly positive, this triggers the corruption of synthesised images with a
    bias field. It is obtained by sampling a first small tensor from a normal distribution, resizing it to full size,
    and rescaling it to positive values by taking the voxel-wise exponential. bias_field_std designates the std dev of
    the normal distribution from which we sample the first tensor. Set to 0 to deactivate bias field corruption.
    :param bias_scale: (optional) If bias_field_std is strictly positive, this designates the ratio between the
    size of the input label maps and the size of the first sampled tensor for synthesising the bias field.
    :param return_gradients: (optional) whether to return the synthetic image or the magnitude of its spatial gradient
    (computed with Sobel kernels).
    """

    # reformat resolutions
    labels_shape = utils.reformat_to_list(labels_shape)
    n_dims, _ = utils.get_dims(labels_shape)
    atlas_res = utils.reformat_to_n_channels_array(atlas_res, n_dims, n_channels)
    data_res = atlas_res if data_res is None else utils.reformat_to_n_channels_array(data_res, n_dims, n_channels)
    thickness = data_res if thickness is None else utils.reformat_to_n_channels_array(thickness, n_dims, n_channels)
    atlas_res = atlas_res[0]
    target_res = atlas_res if target_res is None else utils.reformat_to_n_channels_array(target_res, n_dims)[0]

    # get shapes
    crop_shape, output_shape = get_shapes(labels_shape, output_shape, atlas_res, target_res, output_div_by_n)

    # define model inputs
    labels_input = KL.Input(shape=labels_shape + [1], name='labels_input', dtype='int32')
    means_input = KL.Input(shape=list(generation_labels.shape) + [n_channels], name='means_input')
    stds_input = KL.Input(shape=list(generation_labels.shape) + [n_channels], name='std_devs_input')
    list_inputs = [labels_input, means_input, stds_input]

    # deform labels
    labels = layers.RandomSpatialDeformation(scaling_bounds=scaling_bounds,
                                             rotation_bounds=rotation_bounds,
                                             shearing_bounds=shearing_bounds,
                                             translation_bounds=translation_bounds,
                                             nonlin_std=nonlin_std,
                                             nonlin_scale=nonlin_scale,
                                             inter_method='nearest')(labels_input)

    # cropping
    if crop_shape != labels_shape:
        labels = layers.RandomCrop(crop_shape)(labels)

    # flipping
    if flipping:
        assert aff is not None, 'aff should not be None if flipping is True'
        labels = layers.RandomFlip(get_ras_axes(aff, n_dims)[0], True, generation_labels, n_neutral_labels)(labels)

    # build synthetic image
    image = layers.SampleConditionalGMM(generation_labels)([labels, means_input, stds_input])

    # apply bias field
    if bias_field_std > 0:
        image = layers.BiasFieldCorruption(bias_field_std, bias_scale, False)(image)

    # intensity augmentation
    image = layers.IntensityAugmentation(clip=300, normalise=True, gamma_std=.5, separate_channels=True)(image)

    # loop over channels
    channels = list()
    split = KL.Lambda(lambda x: tf.split(x, [1] * n_channels, axis=-1))(image) if (n_channels > 1) else [image]
    for i, channel in enumerate(split):

        if randomise_res:
            max_res_iso = np.array(utils.reformat_to_list(max_res_iso, length=n_dims, dtype='float'))
            max_res_aniso = np.array(utils.reformat_to_list(max_res_aniso, length=n_dims, dtype='float'))
            max_res = np.maximum(max_res_iso, max_res_aniso)
            resolution, blur_res = layers.SampleResolution(atlas_res, max_res_iso, max_res_aniso)(means_input)
            sigma = l2i_et.blurring_sigma_for_downsampling(atlas_res, resolution, thickness=blur_res)
            channel = layers.DynamicGaussianBlur(0.75 * max_res / np.array(atlas_res), 1.03)([channel, sigma])
            channel = layers.MimicAcquisition(atlas_res, atlas_res, output_shape, False)([channel, resolution])
            channels.append(channel)

        else:
            sigma = l2i_et.blurring_sigma_for_downsampling(atlas_res, data_res[i], thickness=thickness[i])
            channel = layers.GaussianBlur(sigma, 1.03)(channel)
            resolution = KL.Lambda(lambda x: tf.convert_to_tensor(data_res[i], dtype='float32'))([])
            channel = layers.MimicAcquisition(atlas_res, data_res[i], output_shape)([channel, resolution])
            channels.append(channel)

    # concatenate all channels back
    image = KL.Lambda(lambda x: tf.concat(x, -1))(channels) if len(channels) > 1 else channels[0]

    # compute image gradient
    if return_gradients:
        image = layers.ImageGradients('sobel', True, name='image_gradients')(image)
        image = layers.IntensityAugmentation(clip=10, normalise=True)(image)

    # resample labels at target resolution
    if crop_shape != output_shape:
        labels = l2i_et.resample_tensor(labels, output_shape, interp_method='nearest')

    # map generation labels to segmentation values
    labels = layers.ConvertLabels(generation_labels, dest_values=output_labels, name='labels_out')(labels)

    # build model (dummy layer enables to keep the labels when plugging this model to other models)
    image = KL.Lambda(lambda x: x[0], name='image_out')([image, labels])
    brain_model = Model(inputs=list_inputs, outputs=[image, labels])

    return brain_model


def get_shapes(labels_shape, output_shape, atlas_res, target_res, output_div_by_n):

    # reformat resolutions to lists
    atlas_res = utils.reformat_to_list(atlas_res)
    n_dims = len(atlas_res)
    target_res = utils.reformat_to_list(target_res)

    # get resampling factor
    if atlas_res != target_res:
        resample_factor = [atlas_res[i] / float(target_res[i]) for i in range(n_dims)]
    else:
        resample_factor = None

    # output shape specified, need to get cropping shape, and resample shape if necessary
    if output_shape is not None:
        output_shape = utils.reformat_to_list(output_shape, length=n_dims, dtype='int')

        # make sure that output shape is smaller or equal to label shape
        if resample_factor is not None:
            output_shape = [min(int(labels_shape[i] * resample_factor[i]), output_shape[i]) for i in range(n_dims)]
        else:
            output_shape = [min(labels_shape[i], output_shape[i]) for i in range(n_dims)]

        # make sure output shape is divisible by output_div_by_n
        if output_div_by_n is not None:
            tmp_shape = [utils.find_closest_number_divisible_by_m(s, output_div_by_n) for s in output_shape]
            if output_shape != tmp_shape:
                print('output shape {0} not divisible by {1}, changed to {2}'.format(output_shape, output_div_by_n,
                                                                                     tmp_shape))
                output_shape = tmp_shape

        # get cropping and resample shape
        if resample_factor is not None:
            cropping_shape = [int(np.around(output_shape[i]/resample_factor[i], 0)) for i in range(n_dims)]
        else:
            cropping_shape = output_shape

    # no output shape specified, so no cropping unless label_shape is not divisible by output_div_by_n
    else:

        # make sure output shape is divisible by output_div_by_n
        if output_div_by_n is not None:

            # if resampling, get the potential output_shape and check if it is divisible by n
            if resample_factor is not None:
                output_shape = [int(labels_shape[i] * resample_factor[i]) for i in range(n_dims)]
                output_shape = [utils.find_closest_number_divisible_by_m(s, output_div_by_n) for s in output_shape]
                cropping_shape = [int(np.around(output_shape[i] / resample_factor[i], 0)) for i in range(n_dims)]
            # if no resampling, simply check if image_shape is divisible by n
            else:
                cropping_shape = [utils.find_closest_number_divisible_by_m(s, output_div_by_n) for s in labels_shape]
                output_shape = cropping_shape

        # if no need to be divisible by n, simply take cropping_shape as image_shape, and build output_shape
        else:
            cropping_shape = labels_shape
            if resample_factor is not None:
                output_shape = [int(cropping_shape[i] * resample_factor[i]) for i in range(n_dims)]
            else:
                output_shape = cropping_shape

    return cropping_shape, output_shape
