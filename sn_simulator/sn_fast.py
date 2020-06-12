import numpy as np
from astropy.table import Table
import time
import pandas as pd
from sn_tools.sn_calcFast import LCfast
from sn_wrapper.sn_object import SN_Object


class SN(SN_Object):
    """ SN class - inherits from SN_Object
          Input parameters (as given in the input yaml file):
          - SN parameters (x1, color, daymax, z, ...)
          - simulation parameters

         Output:
         - astropy table with the simulated light curve:
               - columns : band, flux, fluxerr, snr_m5,flux_e,zp,zpsys,time
               - metadata : SNID,RA,Dec,DayMax,X1,Color,z

    """

    def __init__(self, param, simu_param, reference_lc=None, gamma=None, mag_to_flux=None):
        super().__init__(param.name, param.sn_parameters, param.gen_parameters,
                         param.cosmology, param.telescope, param.SNID, param.area, param.x0_grid,
                         param.salt2Dir,
                         mjdCol=param.mjdCol, RACol=param.RACol, DecCol=param.DecCol,
                         filterCol=param.filterCol, exptimeCol=param.exptimeCol,
                         m5Col=param.m5Col, seasonCol=param.seasonCol)

        # x1 and color are unique for this simulator
        x1 = np.unique(self.sn_parameters['x1']).item()
        color = np.unique(self.sn_parameters['color']).item()

        """
        # Loading reference file
        fname = '{}/LC_{}_{}_vstack.hdf5'.format(
                self.templateDir, x1, color)

        reference_lc = GetReference(
            fname, self.gammaFile, param.telescope)
        """
        self.reference_lc = reference_lc
        self.gamma = gamma
        self.mag_to_flux = mag_to_flux
        # blue and red cutoffs are taken into account in the reference files

        # SN parameters for Fisher matrix estimation
        self.param_Fisher = ['x0', 'x1', 'color', 'daymax']

        self.lcFast = LCfast(reference_lc, x1, color, param.telescope,
                             param.mjdCol, param.RACol, param.DecCol,
                             param.filterCol, param.exptimeCol,
                             param.m5Col, param.seasonCol, lightOutput=False)

        self.premeta = dict(zip(['x1', 'color', 'x0', ], [x1, color, -1.]))
        for vv in self.param_Fisher:
            vvv = 'epsilon_{}'.format(vv)
            dd = dict(zip([vvv], [np.unique(self.gen_parameters[vvv]).item()]))
            self.premeta.update(dd)

    def __call__(self, obs, display=False, time_display=0):
        """ Simulation of the light curve
        We use multiprocessing (one band per process) to increase speed

        Parameters
        ---------
        obs: array
         array of observations
        gen_par: array
         simulation parameters
        display: bool,opt
         to display LC as they are generated (default: False)
        time_display: float, opt
         time persistency of the displayed window (defalut: 0 sec)

        Returns
        ---------
        astropy table with:
        columns: band, flux, fluxerr, snr_m5,flux_e,zp,zpsys,time
        metadata : SNID,RA,Dec,DayMax,X1,Color,z
        """

        RA = np.mean(obs[self.RACol])
        Dec = np.mean(obs[self.DecCol])
        pixRA = np.unique(obs['pixRA']).item()
        pixDec = np.unique(obs['pixDec']).item()
        pixID = np.unique(obs['healpixID']).item()
        dL = -1

        self.premeta.update(dict(zip(['RA', 'Dec', 'pixRA', 'pixDec', 'healpixID', 'dL'],
                                     [RA, Dec, pixRA, pixDec, pixID, dL])))

        tab_tot = self.lcFast(obs, self.gen_parameters)

        list_tables = self.transform(tab_tot)

        return list_tables

    def transform(self, tab):
        """
        Method to transform a pandas df to a set of astropytables with metedata

        Parameters
        ---------------
        tab: pandas df
          LC points

        Returns
        -----------
        list of astropy tables with metadata

        """

        groups = tab.groupby(['z', 'daymax'])

        tab_tot = []
        for name, grp in groups:
            newtab = Table.from_pandas(grp)
            newtab.meta = dict(zip(['z', 'daymax'], name))
            newtab.meta.update(self.premeta)
            tab_tot.append(newtab)

        return tab_tot
