# Basic packages
import numpy as np
import time
import sys
import os
from pathlib import Path
import logging
import pickle

# Astropy packages
from astropy.table import Table
from astropy.io import fits
import astropy.units as u
# Define the custom unit
n = u.def_unit('n')

# Scipy
import scipy
from scipy.optimize import curve_fit
from scipy import signal
from scipy.interpolate import LinearNDInterpolator


# Matplotlib packages
import matplotlib.pyplot as plt

import warnings
warnings.filterwarnings('ignore', category=UserWarning, append=True)
warnings.filterwarnings('ignore')

working_directory = '/avatar/yanilach/PhD-Home/binaries_galah-main/spectrum_analysis/BinaryAnalysis/'
os.chdir(working_directory)

galah_dr4_directory = '/avatar/buder/GALAH_DR4/'

def load_isochrones():
    global isochrone_interpolator

    print(working_directory)
    if os.path.exists(working_directory + '/assets/parsec_interpolator.pkl'):
        with open(working_directory + '/assets/parsec_interpolator.pkl', 'rb') as f:
            isochrone_interpolator = pickle.load(f)
            return isochrone_interpolator

    else:
        print("No isochrone interpolator found. Creating interpolator - this could take a while (20-40m).")
        isochrone_table = Table.read('BinaryAnalysis/assets/parsec_isochrones_logt_8p00_0p01_10p17_mh_m2p75_0p25_m0p75_mh_m0p60_0p10_0p70_GaiaEDR3_2MASS.fits')

        parsec_points = np.array([isochrone_table['mass'], isochrone_table['logAge'], isochrone_table['m_h']]).T
        parsec_values_lite = np.array([isochrone_table['logT'], isochrone_table['logg'], isochrone_table['logL']]).T

        isochrone_interpolator = LinearNDInterpolator(
            parsec_points,
            parsec_values_lite
        )

        print("Isochrone interpolator created. Saving to file.")

        with open('/assets/parsec_interpolator.pkl', 'wb') as f:
            pickle.dump(isochrone_interpolator, f)

        with open('/assets/parsec_interpolator.pkl', 'rb') as f:
            isochrone_interpolator = pickle.load(f)

        return isochrone_interpolator


def load_neural_network(spectrum):
    global model_name, default_wave_dir, default_model_wave, initial_l, model_components

    # Read in neural network
    model_name = '/avatar/yanilach/PhD-Home/binaries_galah-main/spectrum_modelling/galah_parameter_nn_300_neurons_0p0001_lrate_128_batchsize_model.npz'
    default_wave_dir = '/avatar/yanilach/PhD-Home/binaries_galah-main/spectrum_modelling/galah_parameter_nn_wavelength.txt'
    default_model_wave = np.loadtxt(default_wave_dir, dtype=float)
    initial_l = calculate_default_degrading_wavelength_grid(default_model_wave, spectrum)

    tmp = np.load(model_name)
    w_array_0 = tmp["w_array_0"]
    w_array_1 = tmp["w_array_1"]
    w_array_2 = tmp["w_array_2"]
    b_array_0 = tmp["b_array_0"]
    b_array_1 = tmp["b_array_1"]
    b_array_2 = tmp["b_array_2"]
    x_min = tmp["x_min"]
    x_max = tmp["x_max"]
    tmp.close()

    model_components = (w_array_0, w_array_1, w_array_2, b_array_0, b_array_1, b_array_2, x_min, x_max)


def set_logging_paths(sobject_id):
    global pending_path, failed_path, complete_path
    pending_path = '/home/yanilach/public_html/avatar-tracker/pending/' + str(sobject_id)
    failed_path = '/home/yanilach/public_html/avatar-tracker/failed/' + str(sobject_id)
    complete_path = '/home/yanilach/public_html/avatar-tracker/complete/' + str(sobject_id)
    return pending_path, failed_path, complete_path


def end_processing(msg):
    if os.path.exists(pending_path):
        os.remove(pending_path)
        print(f"File '{pending_path}' deleted successfully." + str(msg))

    with open(failed_path + str('_') + str(msg), 'w') as f:
        pass  # No need to write anything; file will be created empty

    exit()


def read_spectrum(sobject_id, tmass_id, neglect_ir_beginning=True):
    """
    This reads in raw spectra from the GALAH DR4 dataset. Outputs the range of wavelengths with valid CCD data. Does NOT output the observed fluxes.
    Observed and model fluxes are determined during model fitting, as they are dependent on the model for normalisation.
    """

    spectrum = dict()
    spectrum['sobject_id'] = sobject_id # major identifier
    spectrum['flag_sp'] = int(0) # major quality indicator
        
    spectrum['tmass_id'] = str(tmass_id)
    try:
        spectrum['gaiadr3_source_id'] = gaiadr3_source_id
    except:
        spectrum['gaiadr3_source_id'] = int(-1)

    spectrum['flag_sp'] = int(0)
    flag_sp_closest_3x3x3_model_not_available = int(1)
    flag_sp_closest_extra6_model_not_available = int(2)
    flag_sp_no_successful_convergence_within_maximum_loops = int(4)
    flag_sp_not_all_ccds_available = int(8)
    flag_sp_negative_fluxes_in_ccds = int(16)
    flag_sp_negative_resolution_profile = int(32)

    dir = galah_dr4_directory + 'observations/' + str(sobject_id)[:6] + '/spectra/com/' + str(sobject_id) + '1.fits'
    try:
        fits_file = fits.open(dir)
        print("Succsefully found file for object " + dir)
    except:
        print("No file found for spectra", dir)
        end_processing('missing_file')
        exit()
    


    if fits_file[0].header['SLITMASK'] in ['IN','IN      ']:
        spectrum['resolution'] = 'high-res'
        print('Warning: Spectrum is high-resolution!')
    else:
        spectrum['resolution'] = 'low-res'

    if fits_file[0].header['WAV_OK']==0:
        print('Warning: Wavelength solution not ok!')

    if fits_file[0].header['CROSS_OK']==0:
        print('Warning: Cross-talk not calculated reliably!')

    spectrum['plate'] = int(fits_file[0].header['PLATE'])
    
    # This is a test if the CCD is actually available. For 181221001601377, CCD4 is missing for example.
    # We therefore implement a keyword 'available_ccds' to trigger only to loop over the available CCDs
    spectrum['available_ccds'] = []
    
    for ccd in [1,2,3,4]:
        
        try:

            # Try to fill in the basic information for:
            # wavelength (starting wavelength crval and increase of Å/px cdelt)
            # counts (unnormalised flux)
            # counts_unc (unnormalised flux uncertainty)
            # sky
            
            if ccd != 1:
                fits_file = fits.open(galah_dr4_directory+'observations/'+str(sobject_id)[:6]+'/spectra/com/'+str(sobject_id)+str(ccd)+'.fits')

            spectrum['crval_ccd'+str(ccd)] = fits_file[0].header['CRVAL1']
            spectrum['cdelt_ccd'+str(ccd)] = fits_file[0].header['CDELT1']

            spectrum['counts_ccd'+str(ccd)]   = fits_file[0].data
            counts_relative_uncertainty = fits_file[2].data

            bad_counts_unc = np.where(~(counts_relative_uncertainty > 0) == True)[0]
            if len(bad_counts_unc) > 0:
                print('Relative counts uncertainties <= 0 detected for '+str(len(bad_counts_unc))+' pixels in CCD'+str(ccd)+', setting to 0.1 (SNR~10)')
                counts_relative_uncertainty[bad_counts_unc] = 0.1

            spectrum['counts_unc_ccd'+str(ccd)] = counts_relative_uncertainty * fits_file[0].data

            # Read out the line-spread-function; if it is not available, fits_file[7].data will be [0]
            spectrum['lsf_b_ccd'+str(ccd)] = fits_file[0].header['B']
            spectrum['lsf_ccd'+str(ccd)]   = fits_file[7].data

            spectrum['available_ccds'].append(ccd)
        except:
            pass

        if ccd in spectrum['available_ccds']:
            
            # Check if we have the line-spread-function is available.       
            # If not: read in the LSF data for all other fibres and find the closest useful LSF
            
            if np.shape(spectrum['lsf_ccd'+str(ccd)])[0] == 1:
                
                lsf_info = Table.read('../observations/galah_dr4_lsf_info_231004.fits')

                # find all spectra are
                # a) observed with same FIBRE (*pivot*) and
                # b) observed with the same PLATE (*plate*) 
                # c) have a measured LSF in the particular CCD
                # d) have the same resolution setup (low- or high-res)
                if spectrum['resolution'] != 'high-res':
                    same_fibre_plate_ccd_and_has_res_profile = np.where(
                        (
                            (int(str(spectrum['sobject_id'])[-3:]) == lsf_info['pivot']) & 
                            (spectrum['plate'] == lsf_info['plate']) &
                            (lsf_info['res'][:,ccd-1] > 0) & 
                            (lsf_info['reduction_flags'] < 262144)
                        )==True)[0]
                else:
                    same_fibre_plate_ccd_and_has_res_profile = np.where(
                        (
                            (int(str(spectrum['sobject_id'])[-3:]) == lsf_info['pivot']) & 
                            (spectrum['plate'] == lsf_info['plate']) &
                            (lsf_info['res'][:,ccd-1] > 0) & 
                            (lsf_info['reduction_flags'] >= 262144)
                        )==True)[0]

                # Difference between observing runs == abs(sobject_id - all possible sobject_ids)
                sobject_id_differences = np.abs(spectrum['sobject_id'] - lsf_info['sobject_id'][same_fibre_plate_ccd_and_has_res_profile])
                # Now find the closest observing run
                closest_valid_sobject_id_index = np.argmin(sobject_id_differences)
                closest_valid_sobject_id = lsf_info['sobject_id'][same_fibre_plate_ccd_and_has_res_profile][closest_valid_sobject_id_index]

                # replace the relevant LSF for the pixels.
                # Basically assume the LSF between the sobject_ids is the same.
                # This should be reasonable assumption for a stable instrument like HERMES.
                lsf_replacement_fits_file = fits.open(galah_dr4_directory+'observations/'+str(closest_valid_sobject_id)[:6]+'/spectra/com/'+str(closest_valid_sobject_id)+str(ccd)+'.fits')
                spectrum['lsf_b_ccd'+str(ccd)] = lsf_replacement_fits_file[0].header['B']
                spectrum['lsf_ccd'+str(ccd)]   = lsf_replacement_fits_file[7].data
                lsf_replacement_fits_file.close()

                print('No LSF reported for CCD'+str(ccd)+'. Replaced LSF and LSF-B for CCD '+str(ccd)+' with profile from '+str(closest_valid_sobject_id))

            zero_or_negative_flux = np.where(~(spectrum['counts_ccd'+str(ccd)] > 0))
            if len(zero_or_negative_flux) > 10:
                print('Missing/negative flux in more than 10 pixels')
                    
        fits_file.close()

        # We know that the telluric correction for the first half of CCD4 often is bad.
        # This is caused by strong telluric molecular features below 7680 Å.
        # We have therefore implemented a default keyword "neglect_ir_beginning", which cuts out this part:
        
        if (ccd == 4) & (ccd in spectrum['available_ccds']) & neglect_ir_beginning:
            wave_ccd4 = spectrum['crval_ccd4'] + spectrum['cdelt_ccd4'] * np.arange(len(spectrum['counts_ccd4']))
            bad_ir = wave_ccd4 > 7680

            spectrum['crval_ccd4'] = wave_ccd4[bad_ir][0]
            spectrum['counts_ccd4'] = spectrum['counts_ccd4'][bad_ir]
            spectrum['counts_unc_ccd4'] = spectrum['counts_unc_ccd4'][bad_ir]
            spectrum['lsf_ccd4'] = spectrum['lsf_ccd4'][bad_ir]



    # %%
    ### This determines which CCDs have over 95% positive flux and generates an array of wavelengths that will be populated with flux values after.
    ccds_with_positive_flux = []
    for ccd in spectrum['available_ccds']:
        below_0 = spectrum['counts_ccd'+str(ccd)] < 0
        if len(spectrum['counts_ccd'+str(ccd)][below_0])/len(spectrum['counts_ccd'+str(ccd)]) > 0.05:
            print('More than 5% of counts below 0 for CCD'+str(ccd)+'. Neglecting this CCD!')
            if (spectrum['flag_sp'] & flag_sp_negative_fluxes_in_ccds) == 0:
                spectrum['flag_sp'] += flag_sp_negative_fluxes_in_ccds
        else:
            ccds_with_positive_flux.append(ccd)
    spectrum['available_ccds'] = ccds_with_positive_flux

    ccds_with_positive_resolution_profile = []
    for ccd in spectrum['available_ccds']:
        below_0 = np.where(spectrum['lsf_ccd'+str(ccd)] < 0)[0]
        if len(below_0) > 0:
            print('Negative resolution profile detected. Neglecting this CCD!')
            if (spectrum['flag_sp'] & flag_sp_negative_resolution_profile) == 0:
                spectrum['flag_sp'] += flag_sp_negative_resolution_profile
        else:
            ccds_with_positive_resolution_profile.append(ccd)
    spectrum['available_ccds'] = ccds_with_positive_resolution_profile

    # print('Working with the following CCDs: ', spectrum['available_ccds'])

    if (spectrum['available_ccds'] == []):
        if os.path.exists(pending_path):
            os.remove(pending_path)
            print(f"File '{pending_path}' deleted successfully. No CCDs available, incomplete.")

        with open(failed_path + str('_noCCDs'), 'w') as f:
            pass  # No need to write anything; file will be created empty

        exit()

    for ccd in spectrum['available_ccds']:
        spectrum['wave_ccd'+str(ccd)] = spectrum['crval_ccd'+str(ccd)] + spectrum['cdelt_ccd'+str(ccd)]*np.arange(len(spectrum['counts_ccd'+str(ccd)]))
    spectrum['wave'] = np.concatenate(([spectrum['wave_ccd'+str(ccd)] for ccd in spectrum['available_ccds']]))

    ###

    return(spectrum)


def leaky_relu(z):
    return z*(z > 0) + 0.01*z*(z < 0)

def get_spectrum_from_neural_net(scaled_labels, NN_coeffs):
    w_array_0, w_array_1, w_array_2, b_array_0, b_array_1, b_array_2, x_min, x_max = NN_coeffs
    inside = np.einsum('ij,j->i', w_array_0, scaled_labels) + b_array_0
    outside = np.einsum('ij,j->i', w_array_1, leaky_relu(inside)) + b_array_1
    spectrum = np.einsum('ij,j->i', w_array_2, leaky_relu(outside)) + b_array_2
    return spectrum

# %%
def create_synthetic_spectrum(model_parameters, model_labels, default_model=None, default_model_name=None, debug=True, apply_zeropoints=False):
    
    """
    This function creates a synthetic spectrum from a neural network model for each individual star.
    Pass each star's individual labels and paramater values.
    """
    
    if 'teff' in model_labels:
        teff = 1000. * model_parameters['teff']
    else:
        raise ValueError('You have to define Teff as input parameter')
    if 'logg' in model_labels:
        logg = model_parameters['logg']
    else:
        raise ValueError('You have to define logg as input parameter')
    if 'fe_h' in model_labels:
        fe_h = model_parameters['fe_h']
    else:
        raise ValueError('You have to define fe_h as input parameter')

    if 'vmic' in model_labels:
        vmic = model_parameters['vmic']
    else:
        raise ValueError('You have to define vmic as input parameter')

    if 'vsini' in model_labels:
        vsini = model_parameters['vsini']
    else:
        raise ValueError('You have to define vsini as input parameter')

    
    model_labels = np.array([
        teff, logg, fe_h, vmic, vsini
    ])

    scaled_labels = (model_labels - model_components[-2])/(model_components[-1] - model_components[-2]) - 0.5

    model_flux = get_spectrum_from_neural_net(scaled_labels, model_components)

    return(
        model_flux
    )

# %% [markdown]
# ## 2.2) Broaden & interpolate synthetic spectra to match observational data
# 
# The synthetic spectrum is computed at high resolution of R=300,000 to be able to handle different resolutions (the instrumental resolution is expected to change from fibre to fibre, plate to plate, and within the 10 year time, e.g. when the instrument is refocussed).
# 
# In addition, stars rotate. The functions below apply all these effect to the un-broadened synthetic spectrum.

# %%
def sclip(p,fit,n,ye=[],sl=99999,su=99999,min=0,max=0,min_data=1,grow=0,global_mask=None,verbose=True):
    """
    robust normalisation with clipping.
    
    p: array of coordinate vectors. Last line in the array must be values that are fitted. The rest are coordinates.
    fit: name of the fitting function. It must have arguments x,y,ye,and mask and return an array of values of the fitted function at coordinates x
    n: number of iterations
    ye: array of errors for each point
    sl: lower limit in sigma units
    su: upper limit in sigma units
    min: number or fraction of rejected points below the fitted curve
    max: number or fraction of rejected points above the fitted curve
    min_data: minimal number of points that can still be used to make a constrained fit
    global_mask: if initial mask is given it will be used throughout the whole fitting process, but the final fit will be evaluated also in the masked points
    grow: number of points to reject around the rejected point.
    verbose: print the results or not
    
    Taken from GALAH reduction pipeline: https://github.com/sheliak/galah_reduction
    
    Cite Kos et al. (2017) if used:
    https://ui.adsabs.harvard.edu/abs/2017MNRAS.464.1259K/abstract
    
    """

    nv,dim=np.shape(p)

    #if error vector is not given, assume errors are equal to 0:
    if len(ye)==0: ye=np.zeros(dim)
    #if a single number is given for y errors, assume it means the same error is for all points:
    if isinstance(ye, (int, float)): ye=np.ones(dim)*ye

    if global_mask==None: global_mask=np.ones(dim, dtype=bool)
    else: pass

    f_initial=fit(p,ye,global_mask)
    s_initial=np.std(p[-1]-f_initial)

    f=f_initial
    s=s_initial

    tmp_results=[]

    b_old=np.ones(dim, dtype=bool)

    for step in range(n):
        #check that only sigmas or only min/max are given:
        if (sl!=99999 or su!=99999) and (min!=0 or max!=0):
            raise RuntimeError('Sigmas and min/max are given. Only one can be used.')

        #if sigmas are given:
        if sl!=99999 or su!=99999:
            b=np.zeros(dim, dtype=bool)
            if sl>=99999 and su!=sl: sl=su#check if only one is given. In this case set the other to the same value
            if su>=99999 and sl!=su: su=sl

            good_values=np.where(((f-p[-1])<(sl*(s+ye))) & ((f-p[-1])>-(su*(s+ye))))#find points that pass the sigma test
            b[good_values]=True

        #if min/max are given
        if min!=0 or max!=0:
            b=np.ones(dim, dtype=bool)
            if min<1: min=dim*min#detect if min is in number of points or percentage
            if max<1: max=dim*max#detect if max is in number of points or percentage

            bad_values=np.concatenate(((p[-1]-f).argsort()[-int(max):], (p[-1]-f).argsort()[:int(min)]))
            b[bad_values]=False

        #check the grow parameter:
        if grow>=1 and nv==2:
            b_grown=np.ones(dim, dtype=bool)
            for ind,val in enumerate(b):
                if val==False:
                    ind_l=ind-int(grow)
                    ind_u=ind+int(grow)+1
                    if ind_l<0: ind_l=0
                    b_grown[ind_l:ind_u]=False

            b=b_grown

        tmp_results.append(f)

        #check that the minimal number of good points is not too low:
        if len(b[b])<min_data:
            step=step-1
            b=b_old
            break

        #check if the new b is the same as old one and break if yes:
        if np.array_equal(b,b_old):
            step=step-1
            break

        #fit again
        f=fit(p,ye,b&global_mask)
        s=np.std(p[-1][b]-f[b])
        b_old=b

    if verbose:
        print('')
        print('FITTING RESULTS:')
        print('Number of iterations requested:    ',n)
        print('Number of iterations performed:    ', step+1)
        print('Initial standard deviation:        ', s_initial)
        print('Final standard deviation:          ', s)
        print('Number of rejected points:         ',len(np.invert(b[np.invert(b)])))
        print('')

    return f,tmp_results,b

# %%
def calculate_default_degrading_wavelength_grid(default_model_wave, spectrum, synth_res=300000.):
    initial_l = dict()
    
    for ccd in spectrum['available_ccds']:

        wave_model_ccd = (default_model_wave > (3+ccd)*1000) & (default_model_wave < (4+ccd)*1000)

        synth = np.array(default_model_wave[wave_model_ccd]).T

        l_original=synth
        #check if the shape of the synthetic spectrum is correct
        #if synth.shape[1]!=2: logging.error('Syntehtic spectrum must have shape m x 2.')

        #check if the resolving power is high enough
        sigma_synth=synth/synth_res
        if max(sigma_synth)>=min(spectrum['lsf_ccd'+str(ccd)])*0.95:
            logging.error('Resolving power of the synthetic spectrum must be higher.')

            if os.path.exists(pending_path):
                os.remove(pending_path)
                print(f"File '{pending_path}' deleted successfully. No CCDs available, incomplete.")

            with open(failed_path + str('_resolving_power'), 'w') as f:
                pass  # No need to write anything; file will be created empty

            exit()

        #check if wavelength calibration of the synthetic spectrum is linear:
        if not (synth[1]-synth[0])==(synth[-1]-synth[-2]):
            logging.error('Synthetic spectrum must have linear (equidistant) sampling.')		

        #current sampling:
        sampl=synth[1]-synth[0]
        galah_sampl=spectrum['cdelt_ccd'+str(ccd)]

        #original sigma
        s_original=sigma_synth

        #required sigma (resample the resolution map into the wavelength range of the synthetic spectrum)
        s_out=np.interp(synth, spectrum['crval_ccd'+str(ccd)]+spectrum['cdelt_ccd'+str(ccd)]*np.arange(len(spectrum['counts_ccd'+str(ccd)])), spectrum['lsf_ccd'+str(ccd)])
        
        #the sigma of the kernel is:
        s=np.sqrt(s_out**2-s_original**2)
        
        #fit it with the polynomial, so we have a function instead of sampled values:
        map_fit=np.poly1d(np.polyfit(synth, s, deg=6))

        #create an array with new sampling. The first point is the same as in the spectrum:
        l_new=[synth[0]]

        #oversampling. If synthetic spectrum sampling is much finer than the size of the kernel, the code would work, but would return badly sampled spectrum. this is because from here on the needed sampling is measured in units of sigma.
        oversample=galah_sampl/sampl*10.0

        #minimal needed sampling
        min_sampl=max(s_original)/sampl/sampl*oversample
        
        #keep adding samples until end of the wavelength range is reached
        while l_new[-1]<synth[-1]+sampl:
            # THIS IS THE BOTTLENECK OF THE COMPUTATION
            l_new.append(l_new[-1]+map_fit(l_new[-1])/sampl/min_sampl)

        initial_l['ccd'+str(ccd)] = np.array(l_new)
    return(initial_l)



# %%
def galah_kern(fwhm, b):
    """ Returns a normalized 1D kernel as is used for GALAH resolution profile """
    size=2*(fwhm/2.355)**2
    size_grid = int(size) # we limit the size of kernel, so it is as small as possible (or minimal size) for faster calculations
    if size_grid<7: size_grid=7
    x= scipy.mgrid[-size_grid:size_grid+1]
    g = np.exp(-0.693147*np.power(abs(2*x/fwhm), b))
    return g / np.sum(g)

# %%
def synth_resolution_degradation(l, res_map, res_b, synth, initial_l, synth_res=300000.0, reuse_initial_res_wave_grid=True):
    """
    Take a synthetic spectrum with a very high  resolution and degrade its resolution to the resolution profile of the observed spectrum. The synthetic spectrum should not be undersampled, or the result of the convolution might be wrong.
    Parameters:
        synth np array or similar: an array representing the synthetic spectrum. Must have size m x 2. First column is the wavelength array, second column is the flux array. Resolution of the synthetic spectrum must be constant and higher than that of the observed spectrum.
        synth_res (float): resolving power of the synthetic spectrum
    Returns:
        Convolved syntehtic spectrum as a np array of size m x 2.
    """
    
    synth=np.array(synth)
    l_original=synth[:,0]

    #check if the resolving power is high enough
    sigma_synth=synth[:,0]/synth_res
    if max(sigma_synth)>=min(res_map)*0.95: logging.error('Resolving power of the synthetic spectrum must be higher.')
        
    #check if wavelength calibration of the synthetic spectrum is linear:
    if not (synth[:,0][1]-synth[:,0][0])==(synth[:,0][-1]-synth[:,0][-2]):
        logging.error('Synthetic spectrum must have linear (equidistant) sampling.')		

    #current sampling:
    sampl=synth[:,0][1]-synth[:,0][0]
    galah_sampl=l[1]-l[0]

    #original sigma
    s_original=sigma_synth

    #oversampling. If synthetic spectrum sampling is much finer than the size of the kernel, the code would work, but would return badly sampled spectrum. this is because from here on the needed sampling is measured in units of sigma.
    oversample=galah_sampl/sampl*10.0

    if reuse_initial_res_wave_grid == False:        

        #required sigma (resample the resolution map into the wavelength range of the synthetic spectrum)
        s_out=np.interp(synth[:,0], l, res_map)

        #the sigma of the kernel is:
        s=np.sqrt(s_out**2-s_original**2)

        #fit it with the polynomial, so we have a function instead of sampled values:
        map_fit=np.poly1d(np.polyfit(synth[:,0], s, deg=6))

        #create an array with new sampling. The first point is the same as in the spectrum:
        l_new=[synth[:,0][0]]

        #minimal needed sampling
        min_sampl=max(s_original)/sampl/sampl*oversample

        #keep adding samples until end of the wavelength range is reached
        while l_new[-1]<synth[:,0][-1]+sampl:
            # THIS IS THE BOTTLENECK OF THE COMPUTATION
            l_new.append(l_new[-1]+map_fit(l_new[-1])/sampl/min_sampl)
        
        l_new = np.array(l_new)
    else:
        l_new = initial_l
        
    #interpolate the spectrum to the new sampling:
    new_f=np.interp(l_new,synth[:,0],synth[:,1])

    kernel_=galah_kern(max(s_original)/sampl*oversample, res_b)

    con_f=signal.fftconvolve(new_f,kernel_,mode='same')

    return np.array([np.array(l_new),con_f])

# %%
def chebyshev(p,ye,mask):
    coef=np.polynomial.chebyshev.chebfit(p[0][mask], p[1][mask], 4)
    cont=np.polynomial.chebyshev.chebval(p[0],coef)
    return cont

# %%
def cubic_spline_interpolate(old_wavelength, old_flux, new_wavelength):
    """
    INPUT:
    old_wavelength, old_flux: Input spectrum that has to be interpolated
    new_wavelength: Wavelength array onto which we want to interpolate
    
    OUTPUT:
    flux interpolated on new_wavelength array
    """
    return scipy.interpolate.CubicSpline(old_wavelength, old_flux)(new_wavelength)

# %%
def rv_shift(rv_value, wavelength):
    '''
    Shifts observed wavelengths to account for radial velocity measurements
    
    INPUT:
    rv_value = radial velocity in km/s (negative if moving towards earth)
    wavelengths = array of observed wavelengths
    
    OUTPUT:
    array of shifted wavelengths
    '''
    return wavelength / (1.+rv_value/299792.458)

# %% [markdown]
# ## 2.3) Create synthetic spectra for a binary star system

# %%
def create_synthetic_binary_spectrum_at_observed_wavelength(model, spectrum, same_fe_h = True):
    # We use the binary model object to extract the parameters of the two components.
    # The model is updated here and in the get_flux_only call for curve fitting.
    
    if 'f_contr' not in model.model_labels:
        raise ValueError('f_contr has to be part of the model_labels')
    else:
        f_contr = model.params['f_contr']
        
    if 'rv_1' not in model.model_labels:
        raise ValueError('rv_1 has to be part of the model_labels')
    else:
        rv_1 = model.params['rv_1']
        
    if 'rv_2' not in model.model_labels:
        raise ValueError('rv_2 has to be part of the model_labels')
    else:
        rv_2 = model.params['rv_2']    


    component_1_labels = model.get_unique_labels()
    component_1_model_parameter = model.get_component_params(1)

    component_2_labels = model.get_unique_labels()
    component_2_model_parameter = model.get_component_params(2)


#   TODO Fix this part for new object oriented model
    if same_fe_h:
        component_1_labels = np.insert(component_1_labels,3,'fe_h')
        component_2_labels = np.insert(component_2_labels,3,'fe_h')
    
        component_1_model_parameter = np.insert(component_1_model_parameter, 3, model_parameters[model_labels=='fe_h'][0])
        component_2_model_parameter = np.insert(component_2_model_parameter, 3, model_parameters[model_labels=='fe_h'][0])


    # This returns synthetic spectra for each component created by the neural network
    component_1_model = create_synthetic_spectrum(component_1_model_parameter, component_1_labels)
    component_2_model = create_synthetic_spectrum(component_2_model_parameter, component_2_labels)
    
    for ccd in spectrum['available_ccds']:
        
        wave_model_ccd = (default_model_wave > (3+ccd)*1000) & (default_model_wave < (4+ccd)*1000)
        
        wave_model_1_ccd_lsf, component_1_model_ccd_lsf = synth_resolution_degradation(
                l = rv_shift(rv_1,spectrum['wave_ccd'+str(ccd)]), 
                res_map = spectrum['lsf_ccd'+str(ccd)], 
                res_b = spectrum['lsf_b_ccd'+str(ccd)], 
                synth = np.array([default_model_wave[wave_model_ccd], component_1_model[wave_model_ccd]]).T,
                initial_l=initial_l['ccd'+str(ccd)],
                synth_res=300000.0,
                reuse_initial_res_wave_grid = True
            )
    
        wave_model_2_ccd_lsf, component_2_model_ccd_lsf = synth_resolution_degradation(
                l = rv_shift(rv_2,spectrum['wave_ccd'+str(ccd)]), 
                res_map = spectrum['lsf_ccd'+str(ccd)], 
                res_b = spectrum['lsf_b_ccd'+str(ccd)], 
                synth = np.array([default_model_wave[wave_model_ccd], component_2_model[wave_model_ccd]]).T,
                initial_l=initial_l['ccd'+str(ccd)],
                synth_res=300000.0,
                reuse_initial_res_wave_grid = True
            )
        
        component_1_model_ccd_lsf_at_observed_wavelength = cubic_spline_interpolate(
            rv_shift(-rv_1,wave_model_1_ccd_lsf),
            component_1_model_ccd_lsf,
            spectrum['wave_ccd'+str(ccd)]
        )
        component_2_model_ccd_lsf_at_observed_wavelength = cubic_spline_interpolate(
            rv_shift(-rv_2,wave_model_2_ccd_lsf),
            component_2_model_ccd_lsf,
            spectrum['wave_ccd'+str(ccd)]
        )
        
        # Combine the component models via weighting parameter q to get a model flux
        spectrum['flux_model_ccd'+str(ccd)] = f_contr * component_1_model_ccd_lsf_at_observed_wavelength + (1-f_contr) * component_2_model_ccd_lsf_at_observed_wavelength

        renormalisation_fit = sclip((spectrum['wave_ccd'+str(ccd)], spectrum['counts_ccd'+str(ccd)] / spectrum['flux_model_ccd'+str(ccd)]), chebyshev,int(3), ye=spectrum['counts_unc_ccd'+str(ccd)], su=5, sl=5, min_data=100, verbose=False)
        spectrum['flux_obs_ccd'+str(ccd)] = spectrum['counts_ccd'+str(ccd)] / renormalisation_fit[0]
        spectrum['flux_obs_unc_ccd'+str(ccd)] = spectrum['counts_unc_ccd'+str(ccd)] / renormalisation_fit[0]
        

    # Join spectra produced by the CCDs.
    wave = np.concatenate([spectrum['wave_ccd'+str(ccd)] for ccd in spectrum['available_ccds']])
    data = np.concatenate([spectrum['flux_obs_ccd'+str(ccd)] for ccd in spectrum['available_ccds']])
    sigma2 = np.concatenate([spectrum['flux_obs_unc_ccd'+str(ccd)] for ccd in spectrum['available_ccds']])**2
    data_model = np.concatenate([spectrum['flux_model_ccd'+str(ccd)] for ccd in spectrum['available_ccds']])

    # Repack the model parameters into the array
    model_parameters = np.concatenate([[f_contr, rv_1], model.get_component_params(1, values_only=True)[1:], [rv_2], model.get_component_params(2, values_only=True)[1:]])
    model.set_params(model_parameters)

    return(
        wave, data, sigma2, data_model, model
    )

def return_wave_data_sigma_model(model, spectrum, same_fe_h = True, use_solar_spectrum_mask = False):
    
    wave, data, sigma2, data_model, model = create_synthetic_binary_spectrum_at_observed_wavelength(model, spectrum, same_fe_h)

    wave = rv_shift(model.params['rv_1'], wave)

    if use_solar_spectrum_mask:
        # Note: This time we only mask significant outliers, but neglect the line masks
        unmasked = (
                (~((np.abs(data - data_model)/np.sqrt(sigma2) > 5) & (np.abs(data - data_model) > 0.2))) & 
                (~np.any(np.array([((wave >= mask_beginning) & (wave <= mask_end)) for (mask_beginning, mask_end) in zip(masks['mask_begin'],masks['mask_end'])]),axis=0))
            )
    else:
        # Note: This time we only mask significant outliers, but neglect the line masks
        unmasked = (
                (~((np.abs(data - data_model)/np.sqrt(sigma2) > 5) & (np.abs(data - data_model) > 0.2))) #& 
                #(~np.any(np.array([((wave >= mask_beginning) & (wave <= mask_end)) for (mask_beginning, mask_end) in zip(masks['mask_begin'],masks['mask_end'])]),axis=0))
            )

    return(wave, data, sigma2, data_model, unmasked)


def set_iterations(_n):
    global iterations 
    iterations = _n


# %%
def get_flux_only(wave_init, model, spectrum, same_fe_h, unmasked, *model_parameters, plot=False):
    """
    This will be used as interpolation routine to give back a synthetic flux based on the curve_fit parameters
    """
    # Call to GAIA interpolator for teff_1, logg_1 etc.

    # THIS IS THE CRUCIAL LINE THAT UPDATES THE MODEL PARAMETERS
    model.set_params(model_parameters)

    # Override f_contr with the value.

    wave, data, sigma2, model, stellar_model = create_synthetic_binary_spectrum_at_observed_wavelength(model, spectrum, same_fe_h)

    global iterations
    
    if plot:
        iterations += 1
        if iterations % 10 == 0:
        # Plot the wave_init and model
            fig, axes = plt.subplots(1, 10, figsize=(30, 5), sharey=True)

            # Iterate over each line and corresponding subplot
            for i, line in enumerate(important_lines[0:10]):
                # Define the region to plot: line ± 5 Å
                line_wvl = line[0]
                min_wave = line_wvl - 5
                max_wave = line_wvl + 5
                
                # Select data within the specified wavelength range
                mask = (wave >= min_wave) & (wave <= max_wave)
                
                # Plot data and model in the corresponding subplot
                axes[i].plot(wave[mask], data[mask], label='Observed Data')
                axes[i].plot(wave[mask], model[mask], label='Model Fit', linestyle='--')

                difference = abs(model[mask] - data[mask])
                axes[i].plot(wave[mask], difference, label='Model Delta', linestyle='--')
                axes[i].fill_between(wave[mask], 0, difference, color='gray', alpha=0.3)

                
                # Set subplot title and labels
                axes[i].set_title(f'{line[1]} ({line[0]} Å)')
                axes[i].set_xlabel('Wavelength')
                if i == 0:
                    axes[i].set_ylabel('Flux')
                
                # Add legend
                # axes[i].legend()

            # Adjust layout to prevent overlap
            model_agreement_percentage = 100 * np.sum(abs(model - data)) / len(data)
            plt.suptitle(model_agreement_percentage)
            plt.tight_layout()
            plt.show()
        # else:
        #     print(100 * np.sum(abs(model - data)) / len(data))

    return(model[unmasked])

# %%
def load_dr3_lines(mode_dr3_path = 'galah_dr4_important_lines'):
    global important_lines, important_molecules
    """
    
    """
    important_lines = [
        [4861.3230,r'H$_\beta$',r'H$_\beta$'],
        [6562.7970,r'H$_\alpha$',r'H$_\alpha$']
    ]
    
    important_molecules = [
        [4710,4740,'Mol. C2','Mol. C2'],
        [7594,7695,'Mol. O2 (tell.)','Mol. O2 (tell.)']
        ]

    line, wave = np.loadtxt('/avatar/yanilach/PhD-Home/binaries_galah-main/spectrum_analysis/galah_dr4_important_lines',usecols=(0,1),unpack=True,dtype=str, comments=';')

    for each_index in range(len(line)):
        if line[each_index] != 'Sp':
            if len(line[each_index]) < 5:
                important_lines.append([float(wave[each_index]), line[each_index], line[each_index]])
            else:
                important_lines.append([float(wave[each_index]), line[each_index][:-4], line[each_index]])
        
    return(important_lines, important_molecules)


def plot_spectrum(wave,flux,flux_uncertainty,unmasked_region,title_text,comp1_text,comp2_text,neglect_ir_beginning=True):
    """
    Let's plot a spectrum, that is, flux over wavelenth
    
    We will plot 12 different subplot ranges (3 for each CCD) to allow better assessment of the results
    
    INPUT:
    wave : 1D-array with N pixels
    flux : 1D-array with N pixels or (M,N)-array with N pixels for M spectra (e.g. M = 2 for observed and synthetic spectrum)
    """
    
    # Let's define the wavelength beginnings and ends for each suplot
    if neglect_ir_beginning:
        subplot_wavelengths = np.array([
            [4710,4775],
            [4770,4850],
            [4840,4905],
            [5645,5730],
            [5720,5805],
            [5795,5878],
            [6470,6600],
            [6590,6670],
            [6660,6739],
            [7677,7720],
            [7710,7820],
            [7810,7890]
        ])
    else:
        subplot_wavelengths = np.array([
            [4710,4775],
            [4770,4850],
            [4840,4905],
            [5645,5730],
            [5720,5805],
            [5795,5878],
            [6470,6600],
            [6590,6670],
            [6660,6739],
            [7577,7697],
            [7677,7720],
            [7710,7820],
            [7810,7890]
        ])
    
    # How many subplots will we need?
    nr_subplots = np.shape(subplot_wavelengths)[0]
    
    f, gs = plt.subplots(nr_subplots,1,figsize=(8.3,11.7),sharey=True)
    
    try:
        # test if several spectra fed into flux
        dummy = np.shape(flux)[1] == len(wave)
        flux_array_indices = np.shape(flux)[0]
        flux = np.array(flux)
    except:
        flux_array_indices = 1

    # Let's loop over the subplots
    for subplot in range(nr_subplots):
        
        # Which part of the observed/model spectrum is in our subplot wavelength range?
        in_subplot_wavelength_range = (wave > subplot_wavelengths[subplot,0]) & (wave < subplot_wavelengths[subplot,1])

        ax = gs[subplot]
        ax.set_xlim(subplot_wavelengths[subplot,0],subplot_wavelengths[subplot,1])
        
        if len(wave[in_subplot_wavelength_range]) > 0:
            # if only 1 spectrum
            if flux_array_indices == 1:
                ax.plot(wave[in_subplot_wavelength_range],flux[in_subplot_wavelength_range],lw=0.5);
            else:
                for index in range(flux_array_indices):
                    if index == 0:
                        ax.plot(wave[in_subplot_wavelength_range],flux[0,in_subplot_wavelength_range],lw=0.5,c='k',label='data');
                        ax.plot(wave[in_subplot_wavelength_range],1.05 + flux_uncertainty[in_subplot_wavelength_range],lw=0.5,c='C3',label='scatter');
                    if index == 1:
                        ax.plot(wave[in_subplot_wavelength_range],flux[index,in_subplot_wavelength_range],lw=0.5,c='r',label='model (optimised)');
                        ax.plot(wave[in_subplot_wavelength_range],1.05 + np.abs(flux[0,in_subplot_wavelength_range]-flux[index,in_subplot_wavelength_range]),lw=0.5,c='C4',label='residuals');
                if subplot == nr_subplots-1:
                    ax.legend(ncol=2,loc='lower right',fontsize=6)

            maski = 0
            for maski, pixel in enumerate(wave[in_subplot_wavelength_range & unmasked_region]):
                if maski == 0:
                    ax.axvline(pixel,color='C0',alpha=0.1,label='Mask')
                    maski += 1
                else:
                    ax.axvline(pixel,color='C0',alpha=0.1)
            each_index = 0 
            for each_element in important_lines:
                if (each_element[0] > subplot_wavelengths[subplot,0]) & (each_element[0] < subplot_wavelengths[subplot,1]):

                    offset = -0.05+0.1*(each_index%3)
                    each_index+=1
                    ax.axvline(each_element[0],lw=0.2,ls='dashed',c='r')
                    if each_element[1] in ['Li','C','O']:
                        ax.text(each_element[0],offset,each_element[1],fontsize=10,ha='center',color='pink')
                    elif each_element[1] in ['Mg','Si','Ca','Ti','Ti2']:
                        ax.text(each_element[0],offset,each_element[1],fontsize=10,ha='center',color='b')
                    elif each_element[1] in ['Na','Al','K']:
                        ax.text(each_element[0],offset,each_element[1],fontsize=10,ha='center',color='orange')
                    elif each_element[1] in ['Sc','V', 'Cr','Mn', 'Fe', 'Co', 'Ni', 'Cu', 'Zn']:
                        ax.text(each_element[0],offset,each_element[1],fontsize=10,ha='center',color='brown')
                    elif each_element[1] in ['Rb', 'Sr', 'Y', 'Zr', 'Ba', 'La', 'Ce','Mo','Ru', 'Nd', 'Sm','Eu']:
                        ax.text(each_element[0],offset,each_element[1],fontsize=10,ha='center',color='purple')
        ax.set_ylim(-0.1,1.2)
        if subplot == nr_subplots-1:
            ax.set_xlabel(r'Wavelength / $\mathrm{\AA}$')
        ax.set_ylabel('Flux / norm.')
    f.suptitle(title_text+' \n '+comp1_text+' \n '+comp2_text)
    plt.tight_layout(h_pad=0)
    
    return f


class PlottingCallback:
    def __init__(self, wave_init, data_init, model_labels, spectrum, same_fe_h):
        self.iteration = 0
        self.wave_init = wave_init
        self.data_init = data_init
        self.model_labels = model_labels
        self.spectrum = spectrum
        self.same_fe_h = same_fe_h
        self.unmasked = None
        self.model_parameters = None

    def __call__(self, model_parameters):
        self.iteration += 1
        self.model_parameters = model_parameters

        if self.iteration % 100 == 0:
            self.plot_wave_init()

    def plot_wave_init(self):
        # Update unmasked data based on current parameters
        wave, data, sigma2, model, unmasked = return_wave_data_sigma_model(
            self.model_parameters, self.model_labels, self.spectrum, self.same_fe_h
        )
        self.unmasked = unmasked

        # Plot the wave_init and model
        plt.figure(figsize=(10, 5))
        plt.plot(wave[self.unmasked], self.data_init[self.unmasked], label='Observed Data')
        plt.plot(wave[self.unmasked], model[self.unmasked], label='Model Fit', linestyle='--')
        plt.title(f'Iteration {self.iteration}')
        plt.xlabel('Wavelength')
        plt.ylabel('Flux')
        plt.legend()
        plt.show()
