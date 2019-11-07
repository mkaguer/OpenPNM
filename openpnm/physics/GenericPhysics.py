from openpnm.core import Subdomain, ModelsMixin
from openpnm.phases import GenericPhase
from openpnm.geometry import GenericGeometry
from openpnm.utils import Workspace, logging
logger = logging.getLogger(__name__)
ws = Workspace()


class GenericPhysics(Subdomain, ModelsMixin):
    r"""
    This generic class is meant as a starter for custom Physics objects

    It produces a blank object with no pore-scale models attached.  Users can
    add models from the ``models`` module (or create their own).

    Parameters
    ----------
    network : OpenPNM Network object
        The network to which this Physics should be attached

    phase : OpenPNM Phase object
        The Phase object to which this Physics applies

    geometry : OpenPNM Geometry object
        The Geometry object that defines the pores/throats where this Physics
        should be applied.

    name : str, optional
        A unique string name to identify the Physics object, typically same as
        instance name but can be anything.  If left blank, and name will be
        generated that include the class name and a random string.

    """

    def __init__(self, project=None, network=None, phase=None,
                 geometry=None, settings={}, **kwargs):

        # Define some default settings
        self.settings.update({'prefix': 'phys'})
        # Overwrite with user supplied settings, if any
        self.settings.update(settings)

        # Deal with network or project arguments
        if network is not None:
            if project is not None:
                assert network is project.network
            else:
                project = network.project

        super().__init__(project=project, **kwargs)

        network = self.project.network
        if phase is None:
            logger.warning('No phase provided...a GenericPhase' +
                           ' will be created')
            phase = GenericPhase(network=network)
        self.set_phase(phase=phase)

        if geometry is None:
            g = list(self.project.geometries().values())
            if len(g) == 0:
                logger.warning('No geometry provided...a GenericGeometry' +
                               ' will be created')
                geometry = GenericGeometry(network=network,
                                           pores=network.Ps,
                                           throats=network.Ts)
                self.set_geometry(geometry=geometry)
            elif len(g) == 1:
                logger.info('No geometry provided... ' + self.name +
                            ' will be associated with the only available' +
                            ' geometry')
                self.set_geometry(geometry=g[0])
            elif len(g) > 1:
                raise Exception('No geometry provided, but multiple ' +
                                ' geometries exist on project.  Please ' +
                                ' specify which one.')
        else:
            self.set_geometry(geometry=geometry)

    def set_phase(self, phase, mode='add'):
        r"""
        """
        if phase not in self.project:
            raise Exception(self.name + ' not in same project as given phase')
        if mode == 'add':
            phase['pore.'+self.name] = False
            phase['throat.'+self.name] = False
        elif mode == 'remove':
            phase.pop('pore.'+self.name, None)
            phase.pop('throat.'+self.name, None)
        else:
            raise Exception('mode ' + mode + ' not understood')

    def set_geometry(self, geometry):
        r"""
        """
        if geometry not in self.project:
            raise Exception(self.name + ' not in same project as ' +
                            geometry.name)
        network = self.network
        Ps = network.pores(geometry.name)
        Ts = network.throats(geometry.name)
        self._add_locations(pores=Ps, throats=Ts)
