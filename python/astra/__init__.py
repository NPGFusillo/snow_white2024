from inspect import isgeneratorfunction, getfullargspec
from decorator import decorator
from peewee import chunked, IntegrityError, SqliteDatabase
from playhouse.sqlite_ext import SqliteExtDatabase
from sdsstools.configuration import get_config

from astra.utils import log, Timer, flatten

NAME = "astra"
__version__ = "0.4.0"

@decorator
def task(function, *args, **kwargs):
    """
    A decorator for functions that serve as Astra tasks.

    :param function:
        The callable to decorate.

    :param \*args:
        The arguments to the task.

    :param \**kwargs: 
        Keyword arguments for the task and the task decorator. See below.

    :Keyword Arguments:
        * *frequency* (``int``) --
          The number of seconds to wait before saving the results to the database (default: 300).        
        * *batch_size* (``int``) --
          The number of rows to insert per batch (default: 1000).
        * *re_raise_exceptions* (``bool``) -- 
          If `True` (default), exceptions raised in the task will be raised. Otherwise, they will be logged and ignored.
    """
    
    if not isgeneratorfunction(function):
        raise TypeError("Tasks must be generators that `yield` results.")

    frequency = kwargs.pop("frequency", 300)
    batch_size = kwargs.pop("batch_size", 999)
    re_raise_exceptions = kwargs.pop("re_raise_exceptions", True)

    f = function(*args, **kwargs)

    results = []
    with Timer(f, frequency=frequency, attribute_name="t_elapsed") as timer:
        while True:
            try:
                result = next(timer)
                results.append(result)

            except StopIteration:
                break

            except:
                log.exception(f"Exception raised in task {function.__name__}")        
                if re_raise_exceptions:
                    raise
            
            else:
                if timer.check_point:
                    with timer.pause():
                        _bulk_insert(results, batch_size, re_raise_exceptions)
                        # We yield here (instead of earlier) because in SQLite the result won't have a
                        # returning ID if we yield earlier. It's fine in PostgreSQL, but we want to 
                        # have consistent behaviour across backends.
                        yield from results
                        log.debug(f"Yielded {len(results)} results")
                        results = [] # avoid memory leak

    # Write any remaining results to the database.
    _bulk_insert(results, batch_size, re_raise_exceptions)
    yield from results



def _bulk_insert(results, batch_size, re_raise_exceptions=False):
    """
    Insert a batch of results to the database.
    
    :param results:
        A list of records to create (e.g., sub-classes of `astra.models.BaseModel`).
    
    :param batch_size:
        The batch size to use when creating results.
    """
    log.debug(f"Bulk inserting {len(results)} into the database with batch size {batch_size}")
    if not results:
        return None
    
    from astra.models.base import database

    model = results[0].__class__
    if not model.table_exists():
        log.info(f"Creating table {model}")
        model.create_table()
        
    try:
        if isinstance(database, (SqliteExtDatabase, SqliteDatabase)):
            # Do inserts in batches, but make sure that we get the RETURNING id behaviour so that there
            # is consistency in expectations between postgresql/sqlite
            for i, _result in enumerate(database.batch_commit(results, batch_size)):
                # TODO: Not sure why we have to do this,.. but sometimes things try to get re-created?
                if _result.is_dirty():
                    results[i] = model.create(**_result.__data__)
        else:
            with database.atomic():
                model.bulk_create(results, batch_size=batch_size)

    except IntegrityError:
        log.exception(f"Integrity error when saving results to database.")
        # Save the entries to a pickle file so we can figure out what went wrong.
        if re_raise_exceptions:
            raise
        else:
            log.warning(f"We will yield the results, but they are not saved.")

    except:
        log.exception(f"Exception occurred when saving results to database.")
        if re_raise_exceptions:
            raise
        else:
            log.warning(f"We will yield the results, but they are not saved.")
    else:
        log.info(f"Saved {len(results)} results to database.")
    
    return None

try:
    config = get_config(NAME)
    
except FileNotFoundError:
    log.exception(f"No configuration file found for {NAME}:")
