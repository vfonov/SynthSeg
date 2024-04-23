import pandas as pd
import numpy as np


df=(pd.DataFrame(data={'id':np.load("./data/labels_classes_priors/synthseg_segmentation_labels_2.0.npy"),
                      'lab':np.load("./data/labels_classes_priors/synthseg_segmentation_names_2.0.npy")})
    .sort_values(by=['id'])
    .drop_duplicates()
)

df.to_csv("synthseg_labels_v2.0.csv",index=False)
