import sncosmo
import numpy as np
from lsst.sims.photUtils import Bandpass, Sed
from lsst.sims.photUtils import SignalToNoise
from lsst.sims.photUtils import PhotometricParameters
from astropy.table import Table, Column
from lsst.sims.catUtils.dust import EBV
from scipy.interpolate import griddata
import h5py
from sn_wrapper.sn_object import SN_Object
import time
from sn_tools.sn_utils import SNTimer
from sn_tools.sn_calcFast import srand
import pandas as pd
import operator


class SN(SN_Object):
    def __init__(self, param, simu_param, reference_lc=None, gamma=None, mag_to_flux=None):
        super().__init__(param.name, param.sn_parameters, param.gen_parameters,
                         param.cosmology, param.telescope, param.SNID, param.area, param.x0_grid,
                         mjdCol=param.mjdCol, RACol=param.RACol, DecCol=param.DecCol,
                         filterCol=param.filterCol, exptimeCol=param.exptimeCol,
                         nexpCol=param.nexpCol,
                         m5Col=param.m5Col, seasonCol=param.seasonCol,
                         seeingEffCol=param.seeingEffCol, seeingGeomCol=param.seeingGeomCol,
                         airmassCol=param.airmassCol, skyCol=param.skyCol, moonCol=param.moonCol,
                         salt2Dir=param.salt2Dir)

        """ SN class - inherits from SN_Object

            Parameters
            --------------
            param: dict
              parameters requested for the simulation (SN_Object)
            simu_param : dict
              parameters for the simulation:
               name: simulator name (str)
               model: model name for SN (exempla: salt2-extended) (str)
               version: version of the model (str)
      """
        model = simu_param['model']
        version = str(simu_param['version'])
        self.model = model
        self.version = version
        self.gamma = gamma
        self.mag_to_flux = mag_to_flux

        if model == 'salt2-extended':
            model_min = 300.
            model_max = 180000.
            wave_min = 3000.
            wave_max = 11501.

        if model == 'salt2':
            model_min = 3400.
            model_max = 11501.
            wave_min = model_min
            wave_max = model_max

        self.wave = np.arange(wave_min, wave_max, 1.)

        source = sncosmo.get_source(model, version=version)

        self.dustmap = sncosmo.OD94Dust()

        self.lsstmwebv = EBV.EBVbase()

        if self.sn_parameters['dust']:
            self.SN = sncosmo.Model(source=source,
                                    effects=[self.dustmap, self.dustmap],
                                    effect_names=['host', 'mw'],
                                    effect_frames=['rest', 'obs'])
        else:
            self.SN = sncosmo.Model(source=source)
        self.SN.set(z=self.sn_parameters['z'])
        self.SN.set(t0=self.sn_parameters['daymax'] +
                    self.gen_parameters['epsilon_daymax'])
        self.SN.set(c=self.sn_parameters['color'] +
                    self.gen_parameters['epsilon_color'])
        self.SN.set(x1=self.sn_parameters['x1'] +
                    self.gen_parameters['epsilon_x1'])
        # need to correct X0 for alpha and beta
        lumidist = self.cosmology.luminosity_distance(
            self.sn_parameters['z']).value*1.e3
        X0_grid = griddata((self.x0_grid['x1'], self.x0_grid['color']), self.x0_grid['x0_norm'], (
            self.sn_parameters['x1'], self.sn_parameters['color']),  method='nearest')
        X0 = X0_grid / lumidist ** 2
        alpha = 0.13
        beta = 3.
        X0 *= np.power(10., 0.4*(alpha *
                                 self.sn_parameters['x1'] - beta *
                                 self.sn_parameters['color']))
        X0 += self.gen_parameters['epsilon_x0']
        self.X0 = X0
        self.dL = lumidist
        self.SN.set(x0=X0)
        """
        self.SN.set_source_peakabsmag(self.sn_parameters['absmag'],
        self.sn_parameters['band'], self.sn_parameters['magsys'])

        self.X0 = self.SN.get('x0')
        """

        self.defname = dict(zip(['healpixID', 'pixRA', 'pixDec'], [
                            'observationId', param.RACol, param.DecCol]))

        # names for metadata
        self.names_meta = ['SNID', 'RA', 'Dec',
                           'x0', 'epsilon_x0',
                           'x1', 'epsilon_x1',
                           'color', 'epsilon_color',
                           'daymax', 'epsilon_daymax',
                           'z', 'survey_area',
                           'healpixID', 'pixRA', 'pixDec',
                           'season', 'dL', 'ptime', 'status']

        """
        # get the magnitude system here
        self.ab = sncosmo.get_magsystem('ab')

        # band pass
        for band in 'ugrizy':
            bpass = sncosmo.Bandpass(
                self.telescope.atmosphere[band].wavelen, self.telescope.atmosphere[band].sb, name='LSST::'+band, wave_unit=u.nm)
            sncosmo.registry.register(bpass, force=True)
        """

    def __call__(self, obs, display=False, time_display=0.):
        """ Simulation of the light curve

        Parameters
        --------------
        obs: array
          a set of observations
        display: bool, opt
          if True: the simulated LC is displayed
          default: False
        time_display: float
          duration(sec) for which the display is visible
          default: 0

        Returns
        -----------
        astropy table:
        metadata:
          SNID: ID of the supernova(int)
          RA: SN RA(float)
          Dec: SN Dec(float)
          daymax: day of the max luminosity(float)
          epsilon_daymax: epsilon added to daymax for simulation(float)
          x0: SN x0(float)
          epsilon_x0: epsilon added to x0 for simulation(float)
          x1: SN x1(float)
          epsilon_x1: epsilon added to x1 for simulation(float)
          color: SN color(float)
          epsilon_color: epsilon added to color for simulation(float)
          z: SN redshift(float)
          survey_area: survey area for this SN(float)
          pixID: pixel ID
          pixRA: pixel RA
          pixDec: pixel Dec
          season: season
          dL: luminosity distance
        fields:
          flux: SN flux(Jy)
          fluxerr: EN error flux(Jy)
          snr_m5: Signal-to-Noise Ratio(float)
          gamma: gamma parameter(see LSST: From Science...data products eq. 5)(float)
          m5: five-sigma depth(float)
          seeingFwhmEff: seeing eff(float)
          seeingFwhmGeom: seeing geom(float)
          flux_e_sec: flux in pe.s-1 (float)
          mag: magnitude(float)
          exptime: exposure time(float)
          magerr: magg error(float)
          band: filter(str)
          zp: zeropoint(float)
          zpsys: zeropoint system(float)
          time: time(days)(float)
          phase: phase(float)
        """
        # assert (len(np.unique(obs[self.RaCol])) == 1)
        # assert (len(np.unique(obs[self.DecCol])) == 1)
        ra = np.mean(obs[self.RACol])
        dec = np.mean(obs[self.DecCol])
        area = self.area
        season = np.unique(obs['season'])[0]
        pix = {}
        for vv in ['healpixID', 'pixRA', 'pixDec']:
            if vv in obs.dtype.names:
                pix[vv] = np.unique(obs[vv])[0]
            else:
                pix[vv] = np.mean(obs[self.defname[vv]])

        ti = SNTimer(time.time())
        time_ref = time.time()
        time_rof = time.time()
        # Metadata
        """
        names_meta = ['SNID', 'RA', 'Dec',
                      'x0', 'epsilon_x0',
                      'x1', 'epsilon_x1',
                      'color', 'epsilon_color',
                      'daymax', 'epsilon_daymax',
                      'z', 'survey_area',
                      'healpixID', 'pixRA', 'pixDec',
                      'season', 'dL']
        val_meta = [self.SNID, ra, dec,
                    self.X0, self.gen_parameters['epsilon_x0'],
                    self.sn_parameters['x1'], self.gen_parameters['epsilon_x1'],
                    self.sn_parameters['color'], self.gen_parameters['epsilon_color'],
                    self.sn_parameters['daymax'], self.gen_parameters['epsilon_daymax'],
                    self.sn_parameters['z'], area,
                    pix['healpixID'], pix['pixRA'], pix['pixDec'],
                    season, self.dL]

        metadata = dict(zip(names_meta, val_meta))
        """
        # Select obs depending on min and max phases
        obs = self.cutoff(obs, self.sn_parameters['daymax'],
                          self.sn_parameters['z'],
                          self.sn_parameters['min_rf_phase'],
                          self.sn_parameters['max_rf_phase'],
                          self.sn_parameters['blue_cutoff'],
                          self.sn_parameters['red_cutoff'])
        ti(time.time(), 'cutoff')

        """
        print('after sel', obs.dtype)
        for band in np.unique(obs['filter']):
            idx = obs['filter'] == band
            sel = obs[idx]
            phase = (sel['observationStartMJD'] -
                     self.sn_parameters['daymax'])/(1.+self.sn_parameters['z'])
            print(band, np.min(phase), np.max(phase))
        """

        outvals = [self.m5Col, self.mjdCol,
                   self.exptimeCol, self.nexpCol, self.filterCol]
        for bb in [self.airmassCol, self.skyCol, self.moonCol, self.seeingEffCol, self.seeingGeomCol]:
            if bb in obs.dtype.names:
                outvals.append(bb)

        lcdf = pd.DataFrame(obs[outvals])

        print('ici', lcdf[self.mjdCol], self.sn_parameters['daymax'])
        nvals = range(len(lcdf))
        if len(lcdf) == 0:
            ptime = ti.finish(time.time())['ptime'].item()
            table_lc = self.nosim(ra, dec, pix, area, season, ptime, -1)
            return [table_lc]

        # print('bands', np.unique(obs[self.filterCol]))
        for band in 'grizy':
            idb = obs[self.filterCol] == band
        if len(obs) == 0:
            return None, metadata

        # Sort data according to mjd
        obs.sort(order=self.mjdCol)

        # apply dust here since Ra, Dec is known
        """
        ebvofMW = self.lsstmwebv.calculateEbv(
            equatorialCoordinates=np.array(
                [[ra], [dec]]))[0]
        self.SN.set(mwebv=ebvofMW)
        """
        # Get the fluxes (vs wavelength) for each obs
        fluxes = 10.*self.SN.flux(obs[self.mjdCol], self.wave)

        print('after fluxes', fluxes, len(obs))
        ti(time.time(), 'fluxes')

        wavelength = self.wave/10.

        wavelength = np.repeat(wavelength[np.newaxis, :], len(fluxes), 0)
        SED_time = Sed(wavelen=wavelength, flambda=fluxes)

        fluxes = []
        transes = []
        nvals = range(len(SED_time.wavelen))
        # Arrays of SED, transmissions to estimate integrated fluxes
        seds = [Sed(wavelen=SED_time.wavelen[i], flambda=SED_time.flambda[i])
                for i in nvals]
        transes = np.asarray([self.telescope.atmosphere[lcdf.loc[i][self.filterCol]]
                              for i in nvals])
        int_fluxes = np.asarray(
            [seds[i].calcFlux(bandpass=transes[i]) for i in nvals])

        int_fluxes[int_fluxes < 0.] = 1.e-5
        print(int_fluxes)
        lcdf['flux'] = int_fluxes

        # print('after fluxes again ', time.time()-time_ref, int_fluxes)
        ti(time.time(), 'fluxes_b')

        #
        # idx = int_fluxes > 0

        """
        int_fluxes = int_fluxes[idx]
        transes = transes[idx]
        obs = obs[idx]
        """

        # select only positive fluxes
        #idf = lcdf['flux'] > 0
        #lcdf = lcdf[idf]

        nvals = range(len(lcdf))
        if len(lcdf) == 0:
            ptime = ti.finish(time.time())['ptime'].item()
            table_lc = self.nosim(ra, dec, pix, area, season, ptime, -1)
            return [table_lc]

        # magnitudes - integrated  fluxes are in Jy
        lcdf['mag'] = -2.5 * np.log10(lcdf['flux'] / 3631.0)

        # print('mags', time.time()-time_ref)
        ti(time.time(), 'mags')

        time_ref = time.time()

        # estimate SNR
        # Get photometric parameters to estimate SNR
        # magnitude - integrated fluxes are in Jy
        # mag_SN = -2.5 * np.log10(int_fluxes / 3631.0)  # fluxes are in Jy
        photParams = [PhotometricParameters(exptime=vv[self.exptimeCol]/vv[self.nexpCol],
                                            nexp=vv[self.nexpCol]) for index, vv in lcdf.iterrows()]

        """
        photParams = [PhotometricParameters(
            nexp=vv[self.exptimeCol]) for index, vv in lcdf.iterrows()]
        """
        lcdf = lcdf.round({'mag': 4})
        calc = [SignalToNoise.calcSNR_m5(
            lcdf.iloc[i]['mag'], transes[i], lcdf.iloc[i][self.m5Col],
            photParams[i]) for i in nvals]

        """
        for i in nvals:
            print(lcdf.iloc[i]['mag'], transes[i], lcdf.iloc[i][self.m5Col], photParams[i],
                  lcdf.iloc[i][self.exptimeCol]/lcdf.iloc[i][self.nexpCol], lcdf.iloc[i][self.nexpCol])
        """

        gamms = self.telescope.gamma(
            lcdf[self.m5Col], lcdf[self.filterCol], lcdf[self.exptimeCol]/lcdf[self.nexpCol], lcdf[self.nexpCol])

        #print('new gamma', gamms)

        snr_m5 = [calc[i][0] for i in nvals]
        gamma = [calc[i][1] for i in nvals]

        lcdf['snr_m5'] = [calc[i][0] for i in nvals]
        lcdf['gamma'] = [calc[i][1] for i in nvals]
        lcdf['magerr'] = (2.5/np.log(10.))/lcdf['snr_m5']
        lcdf['fluxerr'] = lcdf['flux']/lcdf['snr_m5']

        lcdf = lcdf.groupby([self.filterCol]).apply(
            lambda x: self.gammaint(x)).reset_index()

        #lcdf['gamma_interp'] = gamms
        #print('interp', lcdf['gamma_interp'])
        lcdf['snr_interp'] = 1./srand(
            lcdf['gamma_interp'].values, lcdf['mag'], lcdf[self.m5Col])

        ti(time.time(), 'estimate 1')
        # estimate the flux in elec.sec-1
        lcdf['flux_e_sec'] = self.telescope.mag_to_flux_e_sec(
            lcdf['mag'].values, lcdf[self.filterCol].values, lcdf[self.exptimeCol]/lcdf[self.nexpCol], lcdf[self.nexpCol])[:, 1]

        e_per_sec = lcdf['flux_e_sec'].values

        print(lcdf[['mag', 'flux_e_sec', 'flux_e_sec_int']])

        ti(time.time(), 'all estimates')

        lcdf['zp'] = 2.5*np.log10(3631)
        lcdf['zpsys'] = 'ab'
        lcdf['phase'] = (lcdf[self.mjdCol]-self.sn_parameters['daymax']
                         )/(1.+self.sn_parameters['z'])

        lcdf = lcdf.rename(
            columns={self.mjdCol: 'time', self.filterCol: 'band', self.m5Col: 'm5'})
        lcdf['band'] = 'LSST::'+lcdf['band']
        print('lcdf columns', lcdf.columns)
        print(lcdf)

        # get the processing time
        ptime = ti.finish(time.time())['ptime'].item()

        # transform pandas df to astropy Table
        table_lc = Table.from_pandas(lcdf)
        # set metadata
        table_lc.meta = self.metadata(ra, dec, pix, area, season, ptime, 1)

        # if the user chooses to display the results...
        if display:
            self.plotLC(table_lc['time', 'band',
                                 'flux', 'fluxerr', 'zp', 'zpsys'], time_display)

        # print(test)
        return [table_lc]

    def gammaint(self, grp):
        """
        Method to estimate gamma values from interpolation

        Parameters
        ---------------
        grp: pandas group
          data to process

        Returns
        ----------
        original group with a new col: gamma_int: gamma values

        """
        res = self.gamma[grp.name](
            (grp[self.m5Col].values, grp[self.exptimeCol]/grp[self.nexpCol], grp[self.nexpCol]))
        grp.loc[:, 'gamma_interp'] = res
        grp.loc[:, 'flux_e_sec_int'] = self.mag_to_flux[grp.name](
            (grp['mag'], grp[self.exptimeCol]/grp[self.nexpCol], grp[self.nexpCol]))
        return grp

    def nosim(self, ra, dec, pix, area, season, ptime, status):
        """
        Method to construct an empty table when no simulation was possible

        Parameters
        ---------------
        ra: float
          SN RA
        dec: float
          SN Dec
        pix:
          pixel infos
        area: float
           survey area
        season: int
          season of interest
        ptime: float
           processing time
        status: int
          status of the processing (1=ok, -1=no simu)

        """
        table_lc = Table()
        # set metadata
        table_lc.meta = self.metadata(
            ra, dec, pix, area, season, ptime, status)
        return table_lc

    def metadata(self, ra, dec, pix, area, season, ptime, status):
        """
        Method to fill metadata

        Parameters
        ---------------
        ra: float
          SN ra
        dec: float
          SN dec
        pix: dict
          pixel infos (ID, RA, Dec)
        area: float
           area of the survey
        season: float
           season number
        ptime: float
           processing time
        status: int
          status of the simulation (1=ok, -1=not ok)

        Returns
        -----------
        dict of metadata

        """

        val_meta = [self.SNID, ra, dec,
                    self.X0, self.gen_parameters['epsilon_x0'],
                    self.sn_parameters['x1'], self.gen_parameters['epsilon_x1'],
                    self.sn_parameters['color'], self.gen_parameters['epsilon_color'],
                    self.sn_parameters['daymax'], self.gen_parameters['epsilon_daymax'],
                    self.sn_parameters['z'], area,
                    pix['healpixID'], pix['pixRA'], pix['pixDec'],
                    season, self.dL, ptime, status]

        return dict(zip(self.names_meta, val_meta))
