"""
Searching the best data augmentation on BCIC IV 2a Dataset
====================================================================================

This tutorial shows how to search data augmentations using braindecode.
Indeed, it is known that the best augmentation to use often dependent on the task
or phenomenon studied. Here we follow the methodology proposed in [1] on the
openly available BCI IV 2a Dataset.


.. topic:: Data Augmentation

    Data augmentation could be a step in training deep learning models.
    For decoding brain signals, recent studies have shown that artificially
    generating samples may increase the final performance of a deep learning model [1]_.
    Other studies have shown that data augmentation can be used to cast
    a self-supervised paradigm, presenting a more diverse
    view of the data, both with pretext tasks and contrastive learning [2]_.


Both approaches demand an intense comparison to find the best fit with the data.
This view is supported by Rommel, C., Paillard, J., Moreau, T., & Gramfort, A. (2022),
who demonstrate the importance of the selection the right transformation and
strength for each different type of task considered.
Here, we use the augmentation module present in braindecode in the context of
trialwise decoding with the BCI IV 2a dataset.

References
-----------

.. [1] Rommel, C., Paillard, J., Moreau, T., & Gramfort, A. (2022)
       Data augmentation for learning predictive models on EEG:
       a systematic comparison. https://arxiv.org/abs/2206.14483

.. [2] Banville, H., Chehab, O., Hyvärinen, A., Engemann, D. A., & Gramfort, A. (2021).
       Uncovering the structure of clinical EEG signals with self-supervised learning.
       Journal of Neural Engineering, 18(4), 046020.

.. contents:: This example covers:
   :local:
   :depth: 2

"""

# Authors: Bruno Aristimunha <a.bruno@ufabc.edu.br>
#          Cédric Rommel <cedric.rommel@inria.fr>
# License: BSD (3-clause)

######################################################################
# Loading and preprocessing the dataset
# -------------------------------------
#
# Loading
# ~~~~~~~

from skorch.callbacks import LRScheduler

from braindecode import EEGClassifier
from braindecode.datasets import MOABBDataset

subject_id = 3
dataset = MOABBDataset(dataset_name="BNCI2014001", subject_ids=[subject_id])

######################################################################
# Preprocessing
# ~~~~~~~~~~~~~
#

from braindecode.preprocessing import (
    exponential_moving_standardize, preprocess, Preprocessor, scale)

low_cut_hz = 4.  # low cut frequency for filtering
high_cut_hz = 38.  # high cut frequency for filtering
# Parameters for exponential moving standardization
factor_new = 1e-3
init_block_size = 1000

preprocessors = [
    Preprocessor('pick_types', eeg=True, meg=False, stim=False),  # Keep EEG sensors
    Preprocessor(scale, factor=1e6, apply_on_array=True),  # Convert from V to uV
    Preprocessor('filter', l_freq=low_cut_hz, h_freq=high_cut_hz),  # Bandpass filter
    Preprocessor(exponential_moving_standardize,  # Exponential moving standardization
                 factor_new=factor_new, init_block_size=init_block_size)
]

preprocess(dataset, preprocessors)

######################################################################
# Extracting windows
# ~~~~~~~~~~~~~~~~~~
#

from braindecode.preprocessing import create_windows_from_events
from skorch.helper import SliceDataset
from numpy import array

trial_start_offset_seconds = -0.5
# Extract sampling frequency, check that they are same in all datasets
sfreq = dataset.datasets[0].raw.info['sfreq']
assert all([ds.raw.info['sfreq'] == sfreq for ds in dataset.datasets])
# Calculate the trial start offset in samples.
trial_start_offset_samples = int(trial_start_offset_seconds * sfreq)

# Create windows using braindecode function for this. It needs parameters to
# define how trials should be used.
windows_dataset = create_windows_from_events(
    dataset,
    trial_start_offset_samples=trial_start_offset_samples,
    trial_stop_offset_samples=0,
    preload=True,
)

######################################################################
# Split dataset into train and valid
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Following the rules of the BCI competition
splitted = windows_dataset.split('session')
train_set = splitted['session_T']
eval_set = splitted['session_E']

######################################################################
# Defining a list of transforms
# ------------------------------
#
# In this tutorial, we will use three categories of augmentations.
# This categorization has been proposed by [1]_ to explain and aggregate
# the several possibilities of augmentations in EEG, being them: a) Frequency domain
# augmentations, b) Time domain augmentations, and c) Spatial domain augmentations.
#
# From this same paper, we selected the best augmentations in each type: FTSurrogate,
# SmoothTimeMask, ChannelsDropout, respectively.
#
# For each augmentation, we adjustable two values from a range for one parameter
# inside the transformation.
#
# It is important to remember that you can increase the range.
# For that, we need to define three lists of transformations and range for the parameter
# ∆φmax in FTSurrogate where ∆φmax ∈ [0, 2π); for ∆t in SmoothTimeMask is ∆t ∈ [0, 2];
# For the method ChannelsDropout, we analyse the parameter p_drop ∈ [0, 1].

from numpy import linspace
from braindecode.augmentation import FTSurrogate, SmoothTimeMask, ChannelsDropout

seed = 20200220

transforms_freq = [FTSurrogate(probability=0.5, phase_noise_magnitude=phase_freq,
                               random_state=seed) for phase_freq in linspace(0, 1, 2)]

transforms_time = [SmoothTimeMask(probability=0.5, mask_len_samples=int(sfreq * second),
                                  random_state=seed) for second in linspace(0.1, 2, 2)]

transforms_spatial = [ChannelsDropout(probability=0.5, p_drop=prob,
                                      random_state=seed) for prob in linspace(0, 1, 2)]

######################################################################
# Training a model with data augmentation
# ---------------------------------------
#
# Now that we know how to instantiate three list of ``Transforms``, it is time to learn how
# to use them to train a model and try to search the best for the dataset.
# Let's first create a model for search a parameter.
#
# Create model
# ~~~~~~~~~~~~
#
# The model to be trained is defined as usual.
import torch

from braindecode.util import set_random_seeds
from braindecode.models import ShallowFBCSPNet

cuda = torch.cuda.is_available()  # check if GPU is available, if True chooses to use it
device = 'cuda' if cuda else 'cpu'
if cuda:
    torch.backends.cudnn.benchmark = True

# Set random seed to be able to roughly reproduce results
# Note that with cudnn benchmark set to True, GPU indeterminism
# may still make results substantially different between runs.
# To obtain more consistent results at the cost of increased computation time,
# you can set `cudnn_benchmark=False` in `set_random_seeds`
# or remove `torch.backends.cudnn.benchmark = True`
seed = 20200220
set_random_seeds(seed=seed, cuda=cuda)

n_classes = 4

# Extract number of chans and time steps from dataset
n_channels = train_set[0][0].shape[0]
input_window_samples = train_set[0][0].shape[1]

model = ShallowFBCSPNet(
    n_channels,
    n_classes,
    input_window_samples=input_window_samples,
    final_conv_length='auto',
)

######################################################################
# Create an EEGClassifier with the desired augmentation
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# In order to train with data augmentation, a custom data loader can be
# for the training. Multiple transforms can be passed to it and will be applied
# sequentially to the batched data within the ``AugmentedDataLoader`` object.

from braindecode.augmentation import AugmentedDataLoader

# Send model to GPU
if cuda:
    model.cuda()

##########################################################################
# The model is now trained as in the trial-wise example. The
# ``AugmentedDataLoader`` is used as the train iterator and the list of
# transforms are passed as arguments.

lr = 0.0625 * 0.01
weight_decay = 0

batch_size = 64
n_epochs = 4

clf = EEGClassifier(
    model,
    iterator_train=AugmentedDataLoader,  # This tells EEGClassifier to use a custom DataLoader
    iterator_train__transforms=[],  # This sets is handled by GridSearchCV
    criterion=torch.nn.NLLLoss,
    optimizer=torch.optim.AdamW,
    train_split=None,  # GridSearchCV will control the split and train/validation over the dataset
    optimizer__lr=lr,
    optimizer__weight_decay=weight_decay,
    batch_size=batch_size,
    callbacks=[
        'accuracy',
        ('lr_scheduler', LRScheduler('CosineAnnealingLR', T_max=n_epochs - 1)),
    ],
    device=device,
)

#####################################################################
# To use the skorch framework, it is necessary to transform the windows
# dataset using the module SliceData. Also, it is mandatory to eval the
# generator of the training.

train_X = SliceDataset(train_set, idx=0)
train_y = array(list(SliceDataset(train_set, idx=1)))

#######################################################################
#   Given the trialwise approach, here we use the KFold approach and
#   GridSearchCV.

from sklearn.model_selection import KFold, GridSearchCV

cv = KFold(n_splits=2, shuffle=True, random_state=seed)
fit_params = {'epochs': n_epochs}

transforms = transforms_freq + transforms_time + transforms_spatial

param_grid = {
    'iterator_train__transforms': transforms,
}

clf.verbose = 0

search = GridSearchCV(
    estimator=clf,
    param_grid=param_grid,
    cv=cv,
    return_train_score=True,
    scoring='accuracy',
    refit=True,
    verbose=1,
    error_score='raise')

search.fit(train_X, train_y, **fit_params)

######################################################################
# Analysing the best fit
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# Next, just perform an analysis of the best fit, and the parameters,
# remembering the order that was adjusted.

import pandas as pd
import numpy as np

search_results = pd.DataFrame(search.cv_results_)

best_run = search_results[search_results['rank_test_score'] == 1].squeeze()
best_aug = best_run['params']
validation_score = np.around(best_run['mean_test_score'] * 100, 2).mean()
training_score = np.around(best_run['mean_train_score'] * 100, 2).mean()

report_message = 'Best augmentation is saved in best_aug which gave a mean validation accuracy' + \
                 'of {}% (train accuracy of {}%).'.format(validation_score, training_score)

print(report_message)

eval_X = SliceDataset(eval_set, idx=0)
eval_y = SliceDataset(eval_set, idx=1)
score = search.score(eval_X, eval_y)
print(f'Eval accuracy is {score * 100:.2f}%.')
