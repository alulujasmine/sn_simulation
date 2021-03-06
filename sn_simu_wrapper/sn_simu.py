import numpy as np
from lsst.sims.maf.metrics import BaseMetric
from sn_stackers.coadd_stacker import CoaddStacker
import healpy as hp
import os
import time
import multiprocessing
from astropy.table import Table
import h5py
from astropy.cosmology import w0waCDM
from importlib import import_module
from sn_tools.sn_telescope import Telescope
from sn_simu_wrapper.sn_object import SN_Object
from sn_tools.sn_utils import SimuParameters
from sn_tools.sn_obs import season as seasoncalc
from sn_tools.sn_utils import GetReference, LoadGamma, LoadDust
from scipy.interpolate import interp1d
from sn_tools.sn_io import check_get_dir

class SNSimulation(BaseMetric):
    """LC simulation wrapper class

    Parameters
    ---------------

    mjdCol: str, opt
     mjd col name in observations (default: 'observationStartMJD')
    RACol: str, opt
     RA col name in observations (default: 'fieldRA')
    DecCol:str, opt
     Dec col name in observations (default: 'fieldDec')
    filterCol: str, opt
     filter col name in observations (default: filter')
    m5Col: str, opt
     5-sigma depth col name in observations (default: 'fiveSigmaDepth')
    exptimeCol: str, opt
     exposure time  col name in observations (default: 'visitExposureTime)
    nightCol: str, opt
     night col name in observations (default: 'night')
    obsidCol: str, opt
     observation id col name in observations (default: 'observationId')
    nexpCol: str, opt
     number of exposures col name in observations (default: 'numExposures')
    visitimeCol: str, opt
     visit time col name in observations (default: 'visiTime')
    seeingEffCol: str, opt
     seeing eff col name in observations (default: 'seeingFwhmEff')
    seeingGeomCol: str, opt
     seeing geom  col name in observations (default: 'seeingFwhmGeom')
    coadd: bool, opt
     coaddition of obs (per band and per night) if set to True (default: True)
    config: dict
     configuration dict for simulation (SN parameters, cosmology, telescope, ...)
     ex: {'ProductionID': 'DD_baseline2018a_Cosmo', 'SN parameters': {'Id': 100, 'x1_color': {'type': 'fixed', 'min': [-2.0, 0.2], 'max': [0.2, 0.2], 'rate': 'JLA'}, 'z': {'type': 'uniform', 'min': 0.01, 'max': 0.9, 'step': 0.05, 'rate': 'Perrett'}, 'daymax': {'type': 'unique', 'step': 1}, 'min_rf_phase': -20.0, 'max_rf_phase': 60.0, 'absmag': -19.0906, 'band': 'bessellB', 'magsys': 'vega', 'differential_flux': False}, 'Cosmology': {'Model': 'w0waCDM', 'Omega_m': 0.3, 'Omega_l': 0.7, 'H0': 72.0, 'w0': -1.0, 'wa': 0.0}, 'Instrument': {
         'name': 'LSST', 'throughput_dir': 'LSST_THROUGHPUTS_BASELINE', 'atmos_dir': 'THROUGHPUTS_DIR', 'airmass': 1.2, 'atmos': True, 'aerosol': False}, 'Observations': {'filename': '/home/philippe/LSST/DB_Files/kraken_2026.db', 'fieldtype': 'DD', 'coadd': True, 'season': 1}, 'Simulator': {'name': 'sn_simulator.sn_cosmo', 'model': 'salt2-extended', 'version': 1.0, 'Reference File': 'LC_Test_today.hdf5'}, 'Host Parameters': 'None', 'Display_LC': {'display': True, 'time': 1}, 'Output': {'directory': 'Output_Simu', 'save': True}, 'Multiprocessing': {'nproc': 1}, 'Metric': 'sn_mafsim.sn_maf_simulation', 'Pixelisation': {'nside': 64}}
    x0_norm: array of float
     grid ox (x1,color,x0) values

    """

    def __init__(self, metricName='SNSimulation',
                 mjdCol='observationStartMJD', RACol='fieldRA', DecCol='fieldDec',
                 filterCol='filter', m5Col='fiveSigmaDepth', exptimeCol='visitExposureTime',
                 nightCol='night', obsidCol='observationId', nexpCol='numExposures',
                 vistimeCol='visitTime', seeingEffCol='seeingFwhmEff',
                 airmassCol='airmass',
                 skyCol='sky', moonCol='moonPhase',
                 seeingGeomCol='seeingFwhmGeom',
                 uniqueBlocks=False, config=None, x0_norm=None,**kwargs):

        self.mjdCol = mjdCol
        self.m5Col = m5Col
        self.filterCol = filterCol
        self.RACol = RACol
        self.DecCol = DecCol
        self.exptimeCol = exptimeCol
        self.seasonCol = 'season'
        self.nightCol = nightCol
        self.obsidCol = obsidCol
        self.nexpCol = nexpCol
        self.vistimeCol = vistimeCol
        self.seeingEffCol = seeingEffCol
        self.seeingGeomCol = seeingGeomCol
        self.airmassCol = airmassCol
        self.skyCol = skyCol
        self.moonCol = moonCol

        cols = [self.RACol, self.DecCol, self.nightCol, self.m5Col, self.filterCol, self.mjdCol, self.obsidCol,
                self.nexpCol, self.vistimeCol, self.exptimeCol, self.seeingEffCol, self.seeingGeomCol, self.nightCol,
                self.airmassCol, self.moonCol]

        # self.airmassCol, self.skyCol, self.moonCol]
        self.stacker = None

        coadd = config['Observations']['coadd']

        if coadd:
            # cols += ['sn_coadd']
            self.stacker = CoaddStacker(mjdCol=self.mjdCol,
                                        RACol=self.RACol, DecCol=self.DecCol,
                                        m5Col=self.m5Col, nightCol=self.nightCol,
                                        filterCol=self.filterCol, numExposuresCol=self.nexpCol,
                                        visitTimeCol=self.vistimeCol, visitExposureTimeCol='visitExposureTime')
        super(SNSimulation, self).__init__(
            col=cols, metricName=metricName, **kwargs)

        # bands considered
        self.filterNames = 'ugrizy'

        # grab config file
        self.config = config

        # healpix nside and area
        self.nside = config['Pixelisation']['nside']
        self.area = hp.nside2pixarea(self.nside, degrees=True)

        # prodid
        self.prodid = config['ProductionID']

        # load cosmology
        cosmo_par = config['Cosmology']
        self.cosmology = w0waCDM(H0=cosmo_par['H0'],
                                 Om0=cosmo_par['Omega_m'],
                                 Ode0=cosmo_par['Omega_l'],
                                 w0=cosmo_par['w0'], wa=cosmo_par['wa'])

        # load telescope
        tel_par = config['Instrument']
        self.telescope = Telescope(name=tel_par['name'],
                                   throughput_dir=tel_par['throughput_dir'],
                                   atmos_dir=tel_par['atmos_dir'],
                                   atmos=tel_par['atmos'],
                                   aerosol=tel_par['aerosol'],
                                   airmass=tel_par['airmass'])

        # sn parameters
        self.sn_parameters = config['SN parameters']
        self.gen_par = SimuParameters(self.sn_parameters, cosmo_par, mjdCol=self.mjdCol, area=self.area,
                                      dirFiles=self.sn_parameters['x1_color']['dirFile'],
                                      web_path=config['Web path'])

        # this is for output

        save_status = config['Output']['save']
        self.save_status = save_status
        self.outdir = config['Output']['directory']

        # number of procs to run simu here
        self.nprocs = config['Multiprocessing']['nproc']

        # if saving activated, prepare output dirs
        """
        if self.save_status:
            self.prepareSave(outdir, prodid)
        """
        # simulator parameter
        self.simu_config = config['Simulator']

        # LC display in "real-time"
        self.display_lc = config['Display_LC']['display']
        self.time_display = config['Display_LC']['time']

        # fieldtype, season
        self.field_type = config['Observations']['fieldtype']
        self.season = config['Observations']['season']

        self.type = 'simulation'

        # get the x0_norm values to be put on a 2D(x1,color) griddata
        self.x0_grid = x0_norm

        # SALT2DIR
        self.salt2Dir = self.sn_parameters['salt2Dir']

        # hdf5 index
        self.index_hdf5 = 100

        # load reference LC if simulator is sn_fast
        self.reference_lc = None
        self.gamma = None
        self.mag_to_flux = None
        self.dustcorr = None
        web_path = config['Web path']
        self.error_model = self.simu_config['error_model']
        
        if 'sn_fast' in self.simu_config['name']:
            templateDir = self.simu_config['Template Dir']
            gammaDir = self.simu_config['Gamma Dir']
            gammaFile = self.simu_config['Gamma File']
            dustDir = self.simu_config['DustCorr Dir']

            # x1 and color are unique for this simulator
            x1 = self.sn_parameters['x1']['min']
            color = self.sn_parameters['color']['min']
            bluecutoff = self.sn_parameters['blue_cutoff']
            redcutoff = self.sn_parameters['red_cutoff']
            # Loading reference file
            lcname = 'LC_{}_{}_{}_{}_ebvofMW_0.0_vstack.hdf5'.format(
                x1, color, bluecutoff, redcutoff)

            self.reference_lc = GetReference(templateDir,
                                             lcname, gammaDir, gammaFile, web_path, self.telescope)

            dustFile = 'Dust_{}_{}_{}_{}.hdf5'.format(
                x1, color, bluecutoff, redcutoff)
            self.dustcorr = LoadDust(dustDir, dustFile, web_path).dustcorr

        else:
            gammas = LoadGamma(
                'grizy',  self.simu_config['Gamma Dir'],
                self.simu_config['Gamma File'],
                web_path, self.telescope)

            self.gamma = gammas.gamma
            self.mag_to_flux = gammas.mag_to_flux

        
        if self.error_model:
            SALT2Dir = 'SALT2.Guy10_UV2IR'
            check_get_dir(web_path,SALT2Dir,SALT2Dir)

        self.nprocdict = {}
        self.simu_out = {}
        self.lc_out = {}
        self.SNID = {}
        self.sn_meta = {}

    def run(self, obs, slicePoint=None):
        """ LC simulations

        Parameters
        --------------
        obs: array
          array of observations

        """
        iproc = 1

        if 'iproc' in obs.dtype.names:
            iproc = int(np.mean(obs['iproc']))

        if iproc not in self.nprocdict:
            self.nprocdict[iproc] = iproc
            if self.save_status:
                self.prepareSave(self.outdir, self.prodid, iproc)

        # if 'healpixID' not in obs.dtype.names:
        if slicePoint is not None:
            import numpy.lib.recfunctions as rf
            healpixID = hp.ang2pix(
                slicePoint['nside'], np.rad2deg(slicePoint['ra']), np.rad2deg(slicePoint['dec']), nest=True, lonlat=True)
            pixRA, pixDec = hp.pix2ang(
                self.nside, healpixID, nest=True, lonlat=True)
            obs = rf.append_fields(obs, 'healpixID', [healpixID]*len(obs))
            obs = rf.append_fields(obs, 'pixRA', [pixRA]*len(obs))
            obs = rf.append_fields(obs, 'pixDec', [pixDec]*len(obs))

            print('processing pixel', healpixID)

        # estimate seasons
        obs = seasoncalc(obs)
        # stack if necessary
        if self.stacker is not None:
            obs = self.stacker._run(obs)
        # obs = Observations(data=tab, names=self.names)
        self.fieldname = 'unknown'
        self.fieldid = 0
        if self.season == -1:
            seasons = np.unique(obs[self.seasonCol])
        else:
            seasons = self.season

        time_ref = time.time()
        for seas in seasons:
            self.index_hdf5 += 10000*(seas-1)

            idxa = obs[self.seasonCol] == seas
            obs_season = obs[idxa]
            # remove the u band
            idx = [i for i, val in enumerate(
                obs_season[self.filterCol]) if val[-1] != 'u']

            if len(obs_season[idx]) >= 5:
                self.simuSeason(obs_season[idx], seas, iproc)

        # save metadata
        self.save_metadata(np.unique(obs['healpixID']).item())
        # reset metadata dict
        self.sn_meta[iproc] = {}

        #print('End of simulation', time.time()-time_ref)

    def prepareSave(self, outdir, prodid, iproc):
        """ Prepare output directories for data

        Parameters
        --------------
        outdir: str
         output directory where to copy the data
        prodid: str
         production id (label for input files)
        iproc: int
          internal tag for multiprocessing


        Returns
        ----------
        Two output files are open:
        - astropy table with (SNID,RA,Dec,X1,Color,z) parameters
        -> name: Simu_prodid.hdf5
        - astropy tables with LC
        -> name : LC_prodid.hdf5

        """

        if not os.path.exists(outdir):
            print('Creating output directory', outdir)
            os.makedirs(outdir)
        # Two files  to be opened - tagged by iproc
        # One containing a summary of the simulation:
        # astropy table with (SNID,RA,Dec,X1,Color,z) parameters
        # -> name: Simu_prodid.hdf5
        # A second containing the Light curves (list of astropy tables)
        # -> name : LC_prodid.hdf5

        self.simu_out[iproc] = '{}/Simu_{}_{}.hdf5'.format(
            outdir, prodid, iproc)
        self.lc_out[iproc] = '{}/LC_{}_{}.hdf5'.format(outdir, prodid, iproc)
        self.check_del(self.simu_out[iproc])
        self.check_del(self.lc_out[iproc])
        self.SNID[iproc] = 10**iproc
        self.sn_meta[iproc] = {}

        """
        # and these files will be removed now (before processing)
        # if they exist (to avoid confusions)
        if os.path.exists(self.simu_out):
            os.remove(self.simu_out)
        if os.path.exists(self.lc_out):
            os.remove(self.lc_out)
        """

    def check_del(self, fileName):
        """
        Method to remove a file if already exist

        Parameters
        ----------
        fileName: str
          file to remove (full path)

        """
        if os.path.exists(fileName):
            os.remove(fileName)

    def simuSeason(self, obs, season, iproc):
        """ Generate LC for a season (multiprocessing available) and all simu parameters

        Parameters
        --------------
        obs: array
          array of observations
        season: int
          season number
          iproc: int
          internal tag for multiprocessing

        """

        gen_params = self.gen_par.Params(obs)

        if gen_params is None:
            return

        #self.simuLoop(obs, season, gen_params, iproc)

        npp = self.nprocs
        if 'sn_fast' in self.simu_config['name']:
            npp = 1

        nlc = len(gen_params)
        batch = np.linspace(0, nlc, npp+1, dtype='int')
        
        result_queue = multiprocessing.Queue()

        for i in range(npp):

            ida = batch[i]
            idb = batch[i+1]

            p = multiprocessing.Process(name='Subprocess', target=self.simuLoop, args=(
                obs, season, gen_params[ida:idb], iproc, i, result_queue))
            p.start()

        resultdict = {}
        for j in range(npp):
            resultdict.update(result_queue.get())

        for p in multiprocessing.active_children():
            p.join()

        for j in range(npp):
            metadict = resultdict[j]
            if not self.sn_meta[iproc]:
                self.sn_meta[iproc] = metadict
            else:
                #self.sn_meta[iproc]= self.sn_meta[iproc].update(metadict)
                for key in metadict.keys():
                    self.sn_meta[iproc][key] += metadict[key]

        # self.save_metadata()
        """
        SNID = 100
        for j in range(npp):
            # the output is supposed to be a list of astropytables
            # for each proc: loop on the list to:
            # - get the lc
            # get the metadata

            if self.save_status:
                for lc in resultdict[j]:
                    # number of lc points
                    if lc is not None:
                        SNID += 1
                        self.writeLC(SNID, lc, season)
        """

    def writeLC(self, SNID, lc, season, iproc, meta_lc):
        """
        Method to save lc on disk
        and to update metadata

        Parameters
        ---------------
        SNID: int
         Supernova ID
        lc: astropy Table
           sn light curve
        season: int
          season of observations
        iproc: int
          index for multiprocessing

        """
        # save LC on disk

        epsilon = np.int(1000*1.e8*lc.meta['epsilon_x0'])
        epsilon += np.int(100*1.e8*lc.meta['epsilon_x1'])
        epsilon += np.int(10*1.e8*lc.meta['epsilon_color'])
        epsilon += np.int(1*1.e8*lc.meta['epsilon_daymax'])

        index_hdf5 = self.setIndex(lc.meta['healpixID'],
                                   lc.meta['x1'],
                                   lc.meta['color'],
                                   np.round(lc.meta['z'], 3),
                                   np.round(lc.meta['daymax'], 3),
                                   season, epsilon)

        lc.write(self.lc_out[iproc],
                 path='lc_{}'.format(index_hdf5),
                 append=True,
                 compression=True)

        # build metadata dict
        n_lc_points = len(lc)
        metanames = ['SNID', 'index_hdf5', 'season',
                     'fieldname', 'fieldid', 'n_lc_points', 'area']
        metavals = [SNID, index_hdf5, season,
                    self.fieldname, self.fieldid,
                    n_lc_points, self.area]
        metadict = dict(zip(metanames, metavals))
        metadict.update(lc.meta)

        # update main metadata dict

        """
        if not self.sn_meta[iproc]:
            for key in metadict.keys():
                self.sn_meta[iproc][key] = [metadict[key]]
        else:
            for key in metadict.keys():
                self.sn_meta[iproc][key].extend([metadict[key]])
        """
        if not meta_lc:
            for key in metadict.keys():
                meta_lc[key] = [metadict[key]]
        else:
            for key in metadict.keys():
                meta_lc[key].extend([metadict[key]])

    def setIndex(self, healpixID, x1, color, z, daymax, season, epsilon):

        index_hdf5 = '{}_{}_{}_{}_{}_{}_{}'.format(healpixID,
                                                   x1,
                                                   color,
                                                   z,
                                                   daymax, season, epsilon)

        return index_hdf5

    def simuLoop(self, obs, season, gen_params, iproc, j=0, output_q=None):
        """
        Method to simulate LC

        Parameters
        ---------------
        obs: numpy array
          array of observations
        season: int
          season number
        gen_params: numpy array
          array of observations
        iproc: int
          internal tag for multiprocessing
        j: int
           internal parameter for multiprocessing
        output_q: multiprocessing.Queue()


        """

        time_ref = time.time()
        list_lc = []
        meta_lc = {}
        if 'sn_fast' not in self.simu_config['name']:
            for genpar in gen_params:
                lc = self.simuLCs(obs, season, genpar)
                if lc:
                    list_lc += lc
                # every 20 SN: dump to file
                if len(list_lc) >= 20:
                    self.dump(list_lc, season, iproc, meta_lc)
                    list_lc = []

        else:
            list_lc = self.simuLCs(obs, season, gen_params)

        if len(list_lc) > 0:
            self.dump(list_lc, season, iproc, meta_lc)

        if output_q is not None:
            output_q.put({j: meta_lc})
        else:
            return meta_lc

    def dump(self, list_lc, season, j, meta_lc):
        """
        Method to write a list of lc on disk

        Parameters
        ----------
        list_lc: list
          list of astropy tables
        season: int
          season for observations
        j: int
         tag for multiprocessing

        """
        for lc in list_lc:
            self.SNID[j] += 1
            self.writeLC(self.SNID[j], lc, season, j, meta_lc)

    def simuLCs(self, obs, season, gen_params):
        """ Generate LC for one season and a set of simu parameters

        Parameters
        --------------
        obs: array
          array of observations
        season: int
          season number
        gen_params: dict
           generation parameters
           ex:
        index_hdf5: int
          SN index in hdf5 output file
        output_q: dict
          output for multiprocessing

        Returns
        ----------
        lc_table: astropy table
          table with LC informations (flux, time, ...)
        metadata: dict
          metadata of the simulation
        """
        sn_par = self.sn_parameters.copy()

        for name in ['z', 'x1', 'color', 'daymax']:
            sn_par[name] = gen_params[name]

        SNID = sn_par['Id']
        sn_object = SN_Object(self.simu_config['name'],
                              sn_par,
                              gen_params,
                              self.cosmology,
                              self.telescope, SNID, self.area,
                              x0_grid=self.x0_grid,
                              salt2Dir=self.salt2Dir,
                              mjdCol=self.mjdCol,
                              RACol=self.RACol,
                              DecCol=self.DecCol,
                              filterCol=self.filterCol,
                              exptimeCol=self.exptimeCol,
                              m5Col=self.m5Col)

        module = import_module(self.simu_config['name'])
        simu = module.SN(sn_object, self.simu_config,
                         self.reference_lc, self.gamma, self.mag_to_flux, self.dustcorr,error_model=self.error_model)
        # simulation - this is supposed to be a list of astropytables
        lc_table = simu(obs, self.display_lc, self.time_display)

        return lc_table

    def save_metadata(self, isav=-1):
        """ Copy metadata to disk

        """
        for key, vals in self.sn_meta.items():
            if vals:
                Table(vals).write(
                    self.simu_out[key], 'summary_{}'.format(isav), append=True, compression=True)
