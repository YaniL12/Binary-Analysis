# Binary Analysis
The Binary Analysis repository is designed to analyze binary star systems using observational data and synthetic spectra. This repository includes neural networks to create high-resolution synthetic spectra for the AAT HERMES spectrograph, scripts to run binary star analyses, and tools for post-processing and visualizing the results.

Analysis Pipeline Overview:
- Input suspected binary star - create stellar model object.
- Use stellar parameters found from single star model to generate a binary spectra. Assume same  stellar parameters as the single star model as the initial guesses for component parameters.
- Optimise the residual between the binary model spectrum (generated by the neural network) and observed spectrum iterating over model parameters. Two indivudal spectra are combined through a flux ratio. If teff, logg are not provided, we interpolate these values from an isochrone. This also serves to restrict parameters to physical values.
