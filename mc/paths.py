import os

_path_to_data_dir: str | None = None


def get_path_to_data_dir() -> str:
    global _path_to_data_dir
    if _path_to_data_dir is not None:
        return _path_to_data_dir

    # check env var MC_DATA_DIR
    env_var = os.environ.get("MC_DATA_DIR")
    if env_var is not None:
        # passing quotes is a common mistake
        env_var = env_var.replace("'", "").replace('"', "")
        env_var = os.path.abspath(os.path.normpath(env_var))

        if os.path.isdir(env_var):
            _path_to_data_dir = env_var


        return _path_to_data_dir

    _path_to_data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "data"))
    return _path_to_data_dir
