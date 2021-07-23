
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np

class Net(nn.Module):

    """
    A convolutional neural network for estimating properties of young stellar objects.
    """

    def __init__(self, num_layers=1, num_targets=3, drop_p=0.0):
        super(Net, self).__init__()
        # 3 input channels, 6 output channels, convolution
        # kernel
        self.conv1 = nn.Conv1d(num_layers, 8, 3, padding=1)
        self.conv2 = nn.Conv1d(8, 8, 3, padding=1)
        self.conv3 = nn.Conv1d(8, 16, 3, padding=1)
        self.conv4 = nn.Conv1d(16, 16, 3, padding=1)
        self.conv5 = nn.Conv1d(16, 16, 3, padding=1)
        self.conv6 = nn.Conv1d(16, 16, 3, padding=1)
        self.conv7 = nn.Conv1d(16, 32, 3, padding=1)
        self.conv8 = nn.Conv1d(32, 32, 3, padding=1)
        self.conv9 = nn.Conv1d(32, 32, 3, padding=1)
        self.conv10 = nn.Conv1d(32, 32, 3, padding=1)
        self.conv11 = nn.Conv1d(32, 64, 3, padding=1)
        self.conv12 = nn.Conv1d(64, 64, 3, padding=1)

        # an affine operation: y = Wx + b
        self.fc1 = nn.Linear(64*133*1, 512)
        self.fc1_dropout = nn.Dropout(p=drop_p)
        self.fc2 = nn.Linear(512, 512)
        self.fc3 = nn.Linear(512, num_targets)


    def forward(self, x):
        x = F.max_pool1d(F.relu(self.conv2(F.relu(self.conv1(x)))), 2)
        x = F.max_pool1d(F.relu(self.conv4(F.relu(self.conv3(x)))), 2)
        x = F.max_pool1d(F.relu(self.conv6(F.relu(self.conv5(x)))), 2)
        x = F.max_pool1d(F.relu(self.conv8(F.relu(self.conv7(x)))), 2)
        x = F.max_pool1d(F.relu(self.conv10(F.relu(self.conv9(x)))), 2)
        x = F.max_pool1d(F.relu(self.conv12(F.relu(self.conv11(x)))), 2)
        x = x.view(-1, self.num_flat_features(x))
        x = F.relu(self.fc1_dropout(self.fc1(x)))
        x = F.relu(self.fc1_dropout(self.fc2(x)))
        x = self.fc3(x)
        return x


    def num_flat_features(self, x):
        size = x.size()[1:]  # all dimensions except the batch dimension
        num_features = 1
        for s in size:
            num_features *= s
        return num_features



def predict(model, eval_inputs):
    """
    Predict stellar parameters (teff, logg, [Fe/H]) of young stellar objects, given a spectrum.

    :param model:
        The neural network to use.
    
    :param eval_inputs:
        The spectrum flux.
    """
    
    with torch.no_grad():
        eval_outputs = model.forward(eval_inputs)
    
    eval_outputs = eval_outputs.cpu().numpy()

    # Calculate mean values.
    # TODO: These should not be hard-coded in! They should be stored with the model.
    means = np.array([
        2.880541250669337,
        4716.915128138449,
        -0.22329606176144642
    ])
    sigmas = np.array([
        1.1648147820369943, # LOGG
        733.0099523547299,  # TEFF
        0.3004270650813916, # FE_H
    ])

    # Scale the outputs.
    outputs = eval_outputs * sigmas + means
    
    param_names = ("logg", "teff", "fe_h")
    result = dict(zip(param_names, np.mean(outputs, axis=0)))
    result.update(zip(
        [f"u_{p}" for p in param_names],
        np.std(outputs, axis=0)
    ))

    return result



def create_flux_tensor(
        flux,
        error,
        device,
        num_uncertainty_draws=100,
        dtype=np.float32,
        large_error=1e10
    ):
    """
    Create the requried flux tensor given the spectrum flux and error values,
    and cast it to the given device.

    :param flux:
        The spectrum flux array.
    
    :param error:
        The spectrum error array. This should be the same shape as `flux`.
    
    :param device:
        The name of the torch device to use.
    
    :param num_uncertainty_draws: [optional]
        The number of draws to make of the flux values to propagate the parameter
        uncertainties (default: 100).
    
    :param dtype: [optional]
        The parameter type (default: np.float32).
    
    :param large_error: [optional]
        An arbitrarily large value to assign to 'bad' pixels.
    """
    
    flux = np.atleast_2d(flux).astype(np.float32)
    error = np.atleast_2d(error).astype(np.float32)

    N, P = flux.shape

    bad = ~np.isfinite(flux) + ~np.isfinite(error)
    flux[bad] = np.nanmedian(flux)
    error[bad] = large_error

    flux = torch.from_numpy(flux).to(device)
    error = torch.from_numpy(error).to(device)

    flux_tensor = torch.randn(num_uncertainty_draws, 1, P).to(device)
    median_error = torch.median(error).item()

    error = torch.where(error == large_error, flux, error)
    error_t = torch.tensor(np.array([5 * median_error], dtype=dtype)).to(device)

    error = torch.where(error >= 5 * median_error, error_t, error)

    flux_tensor = flux_tensor * error + flux
    flux_tensor[0][0] = flux

    return flux_tensor

