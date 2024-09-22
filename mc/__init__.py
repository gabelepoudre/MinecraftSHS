import dotenv

# load any .dotenv file in the root directory

dotenv.load_dotenv("../.env")

from . import paths  # noqa
from . import server_runtime  # noqa
from . import update  # noqa
from . import downloads  # noqa

from .server_runtime import ServerRuntime  # noqa
