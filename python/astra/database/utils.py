import json
import os
import sqlalchemy.exc
from tempfile import mktemp
from typing import Dict
from sqlalchemy import (or_, and_, func, distinct)
from astropy.time import Time
from tqdm import tqdm

from astra.database import (astradb, apogee_drpdb, catalogdb, session)
from astra.utils import log, flatten as _flatten


def deserialize_pks(pk, flatten=False):
    """
    Recursively de-serialize input primary keys, which could be in the form of integers, or as
    paths to temporary files that contain integers.
    
    :param pks:
        the input primary keys
    
    :param flatten: [optional]
        return all primary keys as a single flattened list (default: False)
    
    :returns:
        a list of primary keys as integers
    """    
    if isinstance(pk, int):
        v = pk
    elif isinstance(pk, float):
        log.warning(f"Forcing primary key input {pk} as integer")
        v = int(pk)
    elif isinstance(pk, (list, tuple)):
        v = list(map(deserialize_pks, pk))
    elif isinstance(pk, str):
        if os.path.exists(pk):
            with open(pk, "r") as fp:
                contents = json.load(fp)
        else:
            # Need to use double quotes.
            try:
                contents = json.loads(pk.replace("'", '"'))
            except:
                raise ValueError(f"Cannot deserialize primary key of type {type(pk)}: {pk}")
        v = list(map(deserialize_pks, contents))
    else:
        raise ValueError(f"Cannot deserialize primary key of type {type(pk)}: {pk}")
    
    return _flatten([v]) if flatten else v
    

def serialize_pks_to_path(pks, **kwargs):
    keys = ("suffix", "prefix", "dir")
    kwds = { k: kwargs[k] for k in set(keys).intersection(kwargs) }

    path = mktemp(**kwds)
    with open(path, "w") as fp:
        json.dump(pks, fp)
    return path


def serialize(v):
    if isinstance(v, str):
        return v
    else:
        try:
            return json.dumps(v)
        except:
            log.exception(f"Failed to serialize {type(v)}: {v}")
            raise
    


def parse_mjd(
        mjd
    ):
    """
    Parse Modified Julian Date, which might be in the form of an execution date
    from Apache Airflow (e.g., YYYY-MM-DD), or as a MJD integer. The order of
    checks here is:

        1. if it is not a string, just return the input
        2. if it is a string, try to parse the input as an integer
        3. if it is a string and cannot be parsed as an integer, parse it as
           a date time string

    :param mjd:
        the Modified Julian Date, in various possible forms.
    
    :returns:
        the parsed Modified Julian Date
    """
    if isinstance(mjd, str):
        try:
            mjd = int(mjd)
        except:
            return Time(mjd).mjd
    return mjd


def get_sdss4_apstar_kwds(limit=None, **kwargs):
    """
    Get identifying keywords for SDSS-IV APOGEE stars observed.
    """

    # We need; 'apstar', 'apred', 'obj', 'telescope', 'field', 'prefix'
    release, filetype = ("DR16", "apStar")
    columns = (
        catalogdb.SDSSDR16ApogeeStar.apogee_id.label("obj"),
        catalogdb.SDSSDR16ApogeeStar.field,
        catalogdb.SDSSDR16ApogeeStar.telescope,
        catalogdb.SDSSDR16ApogeeStar.apstar_version.label("apstar"),
        catalogdb.SDSSDR16ApogeeStar.file, # for prefix and apred
    )
    q = session.query(*columns).distinct(*columns)
    if kwargs:
        q = q.filter(**kwargs)

    if limit is not None:
        q = q.limit(limit)

    data_model_kwds = []
    for obj, field, telescope, apstar, filename in q.all():

        prefix = filename[:2]
        apred = filename.split("-")[1]

        data_model_kwds.append(dict(
            release=release,
            filetype=filetype,
            obj=obj,
            field=field,
            telescope=telescope,
            apstar=apstar,
            prefix=prefix,
            apred=apred
        ))

    return data_model_kwds
    

def get_sdss5_apstar_kwds(
        mjd, 
        min_ngoodrvs=1,
        limit=None,
    ):
    """
    Get identifying keywords for SDSS-V APOGEE stars observed on the given MJD.

    :param mjd:
        the Modified Julian Date of the observations
    
    :param min_ngoodrvs: [optional]
        the minimum number of good radial velocity measurements (default: 1)
    
    :returns:
        a list of dictionaries containing the identifying keywords for SDSS-V
        APOGEE stars observed on the given MJD, including the `release` and
        `filetype` keys necessary to identify the path
    """
    mjd = parse_mjd(mjd)

    log.debug(f"Parsed input MJD as {mjd}")
    
    # TODO: Consider switching to 'filetype' instead of 'filetype' to be
    #       consistent with SDSSPath.full() argument names.
    release, filetype = ("sdss5", "apStar")
    columns = (
        apogee_drpdb.Star.apred_vers.label("apred"), # TODO: Raise with Nidever
        apogee_drpdb.Star.healpix,
        apogee_drpdb.Star.telescope,
        apogee_drpdb.Star.apogee_id.label("obj"), # TODO: Raise with Nidever
    )

    q = session.query(*columns).distinct(*columns)
    q = q.filter(apogee_drpdb.Star.mjdend == mjd)\
         .filter(apogee_drpdb.Star.ngoodrvs >= min_ngoodrvs)
    if limit is not None:
        q = q.limit(limit)

    log.debug(f"Preparing query {q}")
    rows = q.all()
    log.debug(f"Retrieved {len(rows)} rows")

    keys = [column.name for column in columns]

    kwds = []
    for values in rows:
        d = dict(zip(keys, values))
        d.update(
            release=release,
            filetype=filetype,
            apstar="stars", # TODO: Raise with Nidever
        )
        kwds.append(d)
        
    log.info(f"Retrieved {len(kwds)} identifying keywords for SDSS-V ApStar spectra")

    return kwds


def get_sdss5_apvisit_kwds(mjd_start, mjd_end):
    """
    Get identifying keywords for SDSS-V APOGEE visits taken between the given MJDs
    (end > observed >= start)

    :param mjd_start:
        The starting Modified Julian Date of the observations.
    
    :param mjd_end:
        the ending Modified Julian Date of the observations.
    
    :returns:
        a list of dictionaries containing the identifying keywords for SDSS-V
        APOGEE visits observed on the given MJD, including the `release` and
        `filetype` keys necessary to identify the path
    """

    mjd_start = parse_mjd(mjd_start)
    mjd_end = parse_mjd(mjd_end)

    release, filetype = ("sdss5", "apVisit")
    columns = (
        apogee_drpdb.Visit.apogee_id.label("obj"), # TODO: Raise with Nidever
        apogee_drpdb.Visit.telescope,
        apogee_drpdb.Visit.fiberid.label("fiber"), # TODO: Raise with Nidever
        apogee_drpdb.Visit.plate,
        apogee_drpdb.Visit.field,
        apogee_drpdb.Visit.mjd,
        apogee_drpdb.Visit.apred_vers.label("apred"), # TODO: Raise with Nidever
        apogee_drpdb.Visit.file
    )
    q = session.query(*columns).distinct(*columns)
    q = q.filter(apogee_drpdb.Visit.mjd >= mjd_start)\
         .filter(apogee_drpdb.Visit.mjd < mjd_end)

    total = q.count()
    log.debug(f"Found {q.count()} {release} {filetype} files between MJD {mjd_start} and {mjd_end}")

    kwds = []
    for obj, telescope, fiber, plate, field, mjd, apred, filename in q.all():
        kwds.append(dict(
            obj=obj,
            telescope=telescope,
            release=release,
            filetype=filetype,
            fiber=fiber,
            plate=plate,
            field=field,
            mjd=mjd,
            apred=apred,
            prefix=filename[:2]
        ))
            
    return kwds
    

def get_sdss5_boss_kwds(mjd_start, mjd_end):
    """
    Get identifying keywords for SDSS-V BOSS spectra taken between the given MJDs
    (end > observed >= start)

    :param mjd_start:
        The starting Modified Julian Date of the observations.
    
    :param mjd_end:
        the ending Modified Julian Date of the observations.
    
    :returns:
        a list of dictionaries containing the identifying keywords for SDSS-V
        BOSS spectra observed on the given MJD, including the `release` and
        `filetype` keys necessary to identify the path.
    """    

    mjd_start = parse_mjd(mjd_start)
    mjd_end = parse_mjd(mjd_end)

    release, filetype = ("sdss5", "spSpec")
    columns = (
        catalogdb.SDSSVBossSpall.catalogid,
        catalogdb.SDSSVBossSpall.run2d,
        catalogdb.SDSSVBossSpall.plate,
        catalogdb.SDSSVBossSpall.mjd,
        catalogdb.SDSSVBossSpall.fiberid
    )
    q = session.query(*columns).distinct(*columns)
    q = q.filter(catalogdb.SDSSVBossSpall.mjd >= mjd_start)\
         .filter(catalogdb.SDSSVBossSpall.mjd < mjd_end)

    log.debug(f"Found {q.count()} {release} {filetype} files between MJD {mjd_start} and {mjd_end}")

    kwds = []
    for catalogid, run2d, plate, mjd, fiberid in q.all():
        kwds.append(dict(
            release=release,
            filetype=filetype,
            catalogid=catalogid,
            run2d=run2d,
            plate=plate,
            mjd=mjd,
            fiberid=fiberid
        ))
            
    return kwds


def create_task_instances_for_sdss5_apvisits(
        dag_id,
        task_id,
        run_id,
        mjd_start,
        mjd_end,
        parameters=None,
        return_list=False,
        **kwargs
    ):
    """
    Create task instances for SDSS5 APOGEE visits taken between the given Modified
    Julian Dates (end > observed >= start), with the given identifiers for the 
    directed acyclic graph and the task.

    :param dag_id:
        the identifier string of the directed acyclic graph

    :param task_id:
        the task identifier

    :param mjd_start:
        the starting Modified Julian Date of the observations

    :param mjd_end:
        the ending Modified Julian Date of the observations.
    
    :param parameters: [optional]
        additional parameters to be assigned to the task instances
    """

    parameters = (parameters or dict())

    all_kwds = get_sdss5_apvisit_kwds(mjd_start, mjd_end)

    pks = []
    for kwds in tqdm(all_kwds):
        instance = get_or_create_task_instance(
            dag_id,
            task_id,
            run_id,
            parameters={ **kwds, **parameters}
        )
        pks.append(instance.pk)
    
    if not pks or return_list:
        log.info(f"Returning pks: {pks}")
        return pks
    else:
        path = serialize_pks_to_path(pks, **kwargs)
        log.info(f"Serialized pks to path {path}: {pks}")
        return path


def create_task_instances_for_sdss5_boss(
        dag_id,
        task_id,
        run_id,
        mjd_start,
        mjd_end,
        parameters=None,
        return_list=False,
        **kwargs
    ):
    """
    Create task instances for SDSS5 BOSS visits taken between the given Modified
    Julian Dates (end > observed >= start), with the given identifiers for the 
    directed acyclic graph and the task.

    :param dag_id:
        the identifier string of the directed acyclic graph

    :param task_id:
        the task identifier

    :param mjd_start:
        the starting Modified Julian Date of the observations

    :param mjd_end:
        the ending Modified Julian Date of the observations.
    
    :param parameters: [optional]
        additional parameters to be assigned to the task instances
    """
    parameters = (parameters or dict())

    all_kwds = get_sdss5_boss_kwds(mjd_start, mjd_end)
    
    pks = []
    for kwds in tqdm(all_kwds):
        instance = get_or_create_task_instance(
            dag_id,
            task_id,
            run_id,
            parameters={ **kwds, **parameters}
        )
        pks.append(instance.pk)
    
    if not pks or return_list:
        log.info(f"Returning pks: {pks}")
        return pks
    else:
        path = serialize_pks_to_path(pks, **kwargs)
        log.info(f"Serialized pks to path {path}: {pks}")
        return path


def create_task_instances_for_sdss5_apstars(
        dag_id,
        task_id,
        run_id,
        mjd,
        parameters=None,
        limit=None,
        return_list=False,
        **kwargs
    ):
    """
    Create task instances for SDSS5 APOGEE stars taken on a Modified Julian Date,
    with the given identifiers for the directed acyclic graph and the task.

    :param dag_id:
        the identifier string of the directed acyclic graph

    :param task_id:
        the task identifier

    :param run_id:
        the string of the run identifier

    :param mjd:
        the Modified Julian Date of the observations
    
    :param parameters: [optional]
        additional parameters to be assigned to the task instances
    
    :param limit: [optional]
        limit the number of ApStar objects returned to some number
    
    :param return_list: [optional]
        If True, return a list of primary keys. Otherwise, write those primary 
        keys to a temporary file and return the path (default).
    """

    parameters = (parameters or dict())

    all_kwds = get_sdss5_apstar_kwds(mjd, limit=limit)
    
    pks = []
    for kwds in tqdm(all_kwds):
        instance = get_or_create_task_instance(
            dag_id,
            task_id,
            run_id,
            parameters={ **kwds, **parameters}
        )
        pks.append(instance.pk)
    
    if not pks or return_list:
        log.info(f"Returning pks: {pks}")
        return pks
    else:
        path = serialize_pks_to_path(pks, **kwargs)
        log.info(f"Serialized pks to path {path}: {pks}")
        return path


def create_task_instances_for_sdss5_apstars_from_apvisits(
        dag_id,
        task_id,
        apvisit_pks,
        parameters=None,
        return_list=False,
        **kwargs
    ):
    """
    Create task instances for SDSS-V ApStar objects, given some primary keys for task instances 
    that reference SDSS-V ApVisit objects.

    :param dag_id:
        the identifier string of the directed acyclic graph

    :param task_id:
        the task identifier

    :param apvisit_pks:
        primary keys of task instances that refer to SDSS-V ApVisit objects
    
    :param parameters: [optional]
        additional parameters to be assigned to the task instances
    """

    parameters = parameters or dict()

    # Get the unique stars from the primary keys.
    apvisit_pks = deserialize_pks(apvisit_pks, flatten=True)
    
    log.info(f"Creating task instances for SDSS5 ApStars from ApVisit PKs: {apvisit_pks}")
    
    # Match stars to visits by:
    keys = ("telescope", "obj", "apred")
    
    star_keywords = []
    for pk in apvisit_pks:
        q = session.query(astradb.TaskInstance).filter(astradb.TaskInstance.pk == pk)
        instance = q.one_or_none()

        if instance is None:
            log.warning(f"No task instance found for pk {pk}")
            continue
        
        star_keywords.append([instance.parameters[k] for k in keys])
        
    star_keywords = set(star_keywords)
    log.info(f"Found {len(star_keywords)} unique combinations of {keys}")
    
    # Match these to the apogee_drp.Star table.
    columns = (
        apogee_drpdb.Star.apred_vers.label("apred"), # TODO: Raise with Nidever
        apogee_drpdb.Star.healpix,
        apogee_drpdb.Star.telescope,
        apogee_drpdb.Star.apogee_id.label("obj"), # TODO: Raise with Nidever
    )
    common_kwds = dict(apstar="stars") # TODO: Raise with Nidever

    pks = []
    for telescope, obj, apred in star_keywords:
        q = session.query(apogee_drpdb.Star.healpix)\
                   .distinct(apogee_drpdb.Star.healpix)\
                   .filter(
                       apogee_drpdb.Star.apred_vers == apred,
                       apogee_drpdb.Star.telescope == telescope,
                       apogee_drpdb.Star.apogee_id == obj
                    )
        r = q.one_or_none()
        if r is None: 
            continue
        healpix, = r

        kwds = dict(
            apstar="stars", # TODO: Raise with Nidever
            release="sdss5",
            filetype="apStar",
            healpix=healpix,
            apred=apred,
            telescope=telescope,
            obj=obj,
        )

        instance = get_or_create_task_instance(
            dag_id,
            task_id,
            run_id,
            parameters={ **kwds, **parameters }
        )
        pks.append(instance.pk)

    if not pks or return_list:
        log.info(f"Returning pks: {pks}")
        return pks
    else:
        path = serialize_pks_to_path(pks, **kwargs)
        log.info(f"Serialized pks to path {path}: {pks}")
        return path




def get_task_instance(
        dag_id: str, 
        task_id: str, 
        run_id,
        parameters: Dict,
    ):
    """
    Get a task instance exactly matching the given DAG and task identifiers, and the given parameters.

    :param dag_id:
        The identifier of the directed acyclic graph (DAG).
    
    :param task_id:
        The identifier of the task.

    :param run_id:
        The identifier of the Apache Airflow execution run.
    
    :param parameters:
        The parameters of the task, as a dictionary
    """

    # TODO: Profile this and consider whether it should be used.
    if False:
        # Quick check for things matching dag_id or task_id, which is cheaper than checking all parameters.
        q_ti = session.query(astradb.TaskInstance).filter(
            astradb.TaskInstance.dag_id == dag_id,
            astradb.TaskInstance.task_id == task_id,
            astradb.TaskInstance.run_id == run_id
        )
        if q_ti.count() == 0:
            return None

    # Get primary keys of the individual parameters, and then check by task.
    q_p = session.query(astradb.Parameter.pk).filter(
        or_(*(and_(
            astradb.Parameter.parameter_name == k, 
            astradb.Parameter.parameter_value == serialize(v)
        ) for k, v in parameters.items()))
    )
    N_p = q_p.count()
    if N_p < len(parameters):
        # No task with all of these parameters.
        return None
    
    # Perform subquery to get primary keys of task instances that have all of these parameters.
    sq = session.query(astradb.TaskInstanceParameter.ti_pk)\
                .filter(astradb.TaskInstanceParameter.parameter_pk.in_(pk for pk, in q_p.all()))\
                .group_by(astradb.TaskInstanceParameter.ti_pk)\
                .having(func.count(distinct(astradb.TaskInstanceParameter.parameter_pk)) == N_p).subquery()
            
    # If an exact match is required, combine multiple sub-queries.
    if True:
        sq = session.query(
            astradb.TaskInstanceParameter.ti_pk).join(
                sq,
                astradb.TaskInstanceParameter.ti_pk == sq.c.ti_pk
            )\
            .group_by(astradb.TaskInstanceParameter.ti_pk)\
            .having(func.count(distinct(astradb.TaskInstanceParameter.parameter_pk)) == len(parameters)).subquery()

    # Query for task instances that match the subquery and match our additional constraints.
    q = session.query(astradb.TaskInstance).join(
        sq,
        astradb.TaskInstance.pk == sq.c.ti_pk
    )
    q = q.filter(astradb.TaskInstance.dag_id == dag_id)\
         .filter(astradb.TaskInstance.task_id == task_id)
    if run_id is not None:
        q = q.filter(astradb.TaskInstance.run_id == run_id)


    return q.one_or_none()


def get_or_create_parameter_pk(
        name, 
        value
    ):
    """
    Get or create the primary key for a parameter key/value pair in the database.

    :param name:
        the name of the parameter
    
    :param value:
        the value of the parameter, serialized or not
    
    :returns:
        A two-length tuple containing the integer of the primary key, and a boolean
        indicating whether the entry in the database was created by this function call.
    """

    kwds = dict(parameter_name=name, parameter_value=serialize(value))
    q = session.query(astradb.Parameter).filter_by(**kwds)
    instance = q.one_or_none()
    create = (instance is None)
    if create:
        instance = astradb.Parameter(**kwds)
        try:
            with session.begin(subtransactions=True):
                session.add(instance)

        except sqlalchemy.exc.IntegrityError:

            q = session.query(astradb.Parameter).filter_by(**kwds)
            instance = q.one_or_none()
            if instance is None:
                log.exception(f"Cannot create or retrieve parameter with {kwds}")
                raise
        
    return (instance.pk, create)


def create_task_instance(
        dag_id, 
        task_id, 
        run_id,
        parameters=None
    ):
    """
    Create a task instance in the database with the given identifiers and parameters.

    :param dag_id:
        the identifier string of the directed acyclic graph

    :param task_id:
        the task identifier
    
    :param run_id:
        the string identifiying the Apache Airflow execution run

    :param parameters: [optional]
        a dictionary of parameters to also include in the database
    """

    parameters = (parameters or dict())

    # Get or create the parameter rows first.
    parameter_pks = (pk for pk, created in (get_or_create_parameter_pk(k, v) for k, v in parameters.items()))
    
    # Create task instance.
    ti = astradb.TaskInstance(dag_id=dag_id, task_id=task_id, run_id=run_id,)
    with session.begin():
        session.add(ti)
    
    # Link the parameters.
    with session.begin():
        for parameter_pk in parameter_pks:
            session.add(astradb.TaskInstanceParameter(
                ti_pk=ti.pk,
                parameter_pk=parameter_pk
            ))

    return ti



def add_task_instance_parameter(task_instance, key, value):
    parameter_pk, created = get_or_create_parameter_pk(key, value)
    with session.begin():
        # TODO: Check if the task instance already has this parameter.
        session.add(astradb.TaskInstanceParameter(ti_pk=task_instance.pk, parameter_pk=parameter_pk))
    log.debug(f"Added key/value pair {key}: {value} to task instance {task_instance}")
    return parameter_pk
    

def del_task_instance_parameter(task_instance, key):
    try:
        value = task_instance.parameters[key]
    except KeyError:
        # That key isn't in there!
        None
    else:
        # Get the PK.
        parameter_pk, _ = get_or_create_parameter_pk(key, value)

        # Get the TI/PK
        q = session.query(astradb.TaskInstanceParameter).filter(
            astradb.TaskInstanceParameter.ti_pk == task_instance.pk, 
            astradb.TaskInstanceParameter.parameter_pk == parameter_pk
        ).one_or_none()

        session.delete(q)
        log.debug(f"Removed key/value pair {key}: {value} from task instance {task_instance}")

    assert key not in task_instance.parameters
    return True


def update_task_instance_parameters(task_instance, **params):
    for key, value in params.items():
        del_task_instance_parameter(task_instance, key)
        add_task_instance_parameter(task_instance, key, value)
    return True





def get_or_create_task_instance(
        dag_id, 
        task_id,
        run_id,
        parameters=None
    ):
    """
    Get or create a task instance given the identifiers of the directed acyclic graph, the task,
    and the parameters of the task instance.

    :param dag_id:
        the identifier string of the directed acyclic graph

    :param task_id:
        the task identifier

    :param run_id:
        the identifier of the Apache Airflow run.
    
    :param parameters: [optional]
        a dictionary of parameters to also include in the database
    """
    
    parameters = (parameters or dict())

    #instance = get_task_instance(dag_id, task_id, run_id, parameters)
    instance = None
    if instance is None:
        return create_task_instance(dag_id, task_id, run_id, parameters)
    return instance


def create_task_output(
        task_instance_or_pk, 
        model, 
        **kwargs
    ):
    """
    Create a new entry in the database for the output of a task.

    :param task_instance_or_pk:
        the task instance (or its primary key) to reference this output to
        
    :param model:
        the database model to store the result (e.g., `astra.database.astradb.Ferre`)
    
    :param \**kwargs:
        the keyword arguments that will be stored in the database
    
    :returns:
        A two-length tuple containing the task instance, and the output instance
    """

    # Get the task instance.
    if not isinstance(task_instance_or_pk, astradb.TaskInstance):
        task_instance = session.query(astradb.TaskInstance)\
                               .filter(astradb.TaskInstance.pk == task_instance_or_pk)\
                               .one_or_none()
                            
        if task_instance is None:
            raise ValueError(f"no task instance found matching primary key {task_instance_pk}")
    else:
        task_instance = task_instance_or_pk

    # Create a new output interface entry.
    with session.begin():
        output_interface = astradb.OutputInterface()
        session.add(output_interface)
    
    assert output_interface.pk is not None

    # Include the task instance PK so that if the output for that task instance is
    # later updated, then we can still find historical outputs.
    kwds = dict(ti_pk=task_instance.pk, output_pk=output_interface.pk)
    kwds.update(kwargs)

    # Create the instance of the result.
    output_result = model(**kwds)
    with session.begin():
        session.add(output_result)

    # Reference the output to the task instance.
    with session.begin():
        task_instance.output_pk = output_interface.pk

    assert task_instance.output_pk is not None
    #log.info(f"Created output {output_result} for task instance {task_instance}")
    return output_result