"""
Magic Singleton class to be used as a metaclass, via:
https://stackoverflow.com/questions/6760685/creating-a-singleton-in-python

"""


class Singleton(type):
    """
    Magic Singleton class to be used as a metaclass, via:
    https://stackoverflow.com/questions/6760685/creating-a-singleton-in-python

    To use, simply create a class and set its metaclass=Singleton

    """
    _instances: dict = {}

    def __call__(cls, *args, **kwargs):
        """__call__ magic"""
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]


class RSingleton(type):
    """
    Magic Reinitializing (recalls __init__ on subsequent creations) Singleton class to be used as a metaclass, via:
    https://stackoverflow.com/questions/6760685/creating-a-singleton-in-python

    To use, simply create a class and set its metaclass=Singleton

    """
    _instances: dict = {}

    def __call__(cls, *args, **kwargs):
        """__call__ magic"""
        if cls not in cls._instances:
            cls._instances[cls] = super(RSingleton, cls).__call__(*args, **kwargs)
        else:
            cls._instances[cls].__init__(*args, **kwargs)
        return cls._instances[cls]
