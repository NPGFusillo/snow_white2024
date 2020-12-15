
import os
from astra.utils import log
from astra.contrib.ferre.core import (Ferre as FerreNoQueue, FerreSlurmQueue)
from astra.contrib.ferre.tasks.mixin import (FerreMixin, SourceMixin)
#from astra.contrib.ferre.tasks.targets import GridHeaderTarget
from astropy.table import Table


class FerreBase(FerreMixin, SourceMixin):


    def output(self):
        raise NotImplementedError("this should be over-written by sub-classes")

    def requires(self):
        """ The requirements of this task. """
        raise NotImplementedError("this should be over-written by sub-classes")
    

    def get_directory_kwds(self):
        """ Get the keywords for creating a directory for FERRE to run in. """
        directory_kwds = dict(self.directory_kwds or {})
        directory_kwds.setdefault(
            "dir",
            os.path.join(self.output_base_dir, "scratch")
        )
        return directory_kwds


    def get_ferre_model(self):
        """ Get the model class to run FERRE, depending on whether you are running through Slurm or not. """

        kwds = self.get_ferre_kwds()

        if not self.slurm_kwds:
            Ferre = FerreNoQueue
        else:
            Ferre = FerreSlurmQueue

            # Include task identifier as label.
            slurm_kwds = dict(label=self.task_id)
            slurm_kwds.update(self.slurm_kwds)

            kwds.update(slurm_kwds=slurm_kwds)

        return Ferre(**kwds)
            
    
    def get_ferre_kwds(self):
        """ Return human-readable keywords that will be used with FERRE. """
        return {
            "grid_header_path": self.grid_header_path,
            "interpolation_order": self.interpolation_order,
            "init_algorithm_flag": self.init_algorithm_flag,
            "error_algorithm_flag": self.error_algorithm_flag,
            "continuum_flag": self.continuum_flag,
            "continuum_order": self.continuum_order,
            "continuum_reject": self.continuum_reject,
            "continuum_observations_flag": self.continuum_observations_flag,
            "full_covariance": self.full_covariance,
            "pca_project": self.pca_project,
            "pca_chi": self.pca_chi,
            "frozen_parameters": self.frozen_parameters,
            "optimization_algorithm_flag": self.optimization_algorithm_flag,
            "wavelength_interpolation_flag": self.wavelength_interpolation_flag,
            "lsf_shape_flag": self.lsf_shape_flag,
            "input_weights_path": self.input_weights_path,
            "input_lsf_path": self.input_lsf_path,
            "use_direct_access": self.use_direct_access,
            "n_threads": self.n_threads,
            "debug": self.debug,
            "directory_kwds": self.get_directory_kwds(),
            "executable": self.ferre_executable,
            "ferre_kwds": self.ferre_kwds
        }


    def get_source_names(self):
        """ Return a list of source names for convenience in FERRE. """
        return list(map(str, range(self.get_batch_size())))
    

    def read_input_observations(self):
        raise NotImplementedError("this should be implemented by the sub-classes")


    def execute(self):
        """ Execute FERRE. """

        N = self.get_batch_size()
        log.info(f"Running {N} task{('s in batch mode' if N > 1 else '')}: {self}")

        spectra = self.read_input_observations()

        model = self.get_ferre_model()
        
        results = model.fit(
            spectra,
            initial_parameters=self.initial_parameters,
            names=self.get_source_names(),
            full_output=True
        )

        return (model, spectra, results)


    def run(self):
        """ Run this task. """
        
        model, spectra, (p_opt, p_cov, meta) = self.execute()

        for i, (task, spectrum) in enumerate(zip(self.get_batch_tasks(), spectra)):

            # Write result to database.
            result = dict(zip(model.parameter_names, p_opt[i]))
            result.update(
                log_snr_sq=meta["log_snr_sq"][i],
                log_chisq_fit=meta["log_chisq_fit"][i]
            )

            # Write to database as required.
            if "database" in task.output():
                task.output()["database"].write(result)

            # Write an AstraSource object as required.
            if "AstraSource" in task.output():

                # Get the continuum used by FERRE.
                continuum = meta["continuum"][i]
                task.output()["AstraSource"].write(
                    spectrum,
                    normalized_flux=spectrum.flux.value / continuum,
                    normalized_ivar=continuum * spectrum.uncertainty.array * continuum,
                    continuum=continuum,
                    model_flux=meta["model_flux"][i],
                    model_ivar=None,
                    results_table=Table(rows=[result]),
                )

        model.teardown()

        return None

