import scipy as sp
import numpy as np
import scipy.spatial as sptl
import matplotlib.pyplot as plt
from transforms3d import _gohlketransforms as tr
from scipy import ndimage
import math
from skimage.morphology import convex_hull_image
from skimage.measure import regionprops
from openpnm import topotools
from openpnm.network import DelaunayVoronoiDual
from openpnm.core import logging
from openpnm.geometry import models as gm
from openpnm.geometry import GenericGeometry
from openpnm.utils.misc import unique_list
import openpnm.utils.vertexops as vo
from scipy.stats import itemfreq
logger = logging.getLogger(__name__)


class VoronoiFibers(DelaunayVoronoiDual):
    r"""

    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        VoronoiGeometry(network=self, pores=self.pores('delaunay'),
                        throats=self.throats('delaunay'),
                        name=self.name+'_del')


class VoronoiGeometry(GenericGeometry):
    r"""
    Voronoi subclass of GenericGeometry.

    Parameters
    ----------
    name : string
        A unique name for the network

    fibre_rad: float
        Fibre radius to apply to Voronoi edges when calculating pore and throat
        sizes

    voxel_vol : boolean
        Determines whether to calculate pore volumes by creating a voxel image
        or to use the offset vertices of the throats. Voxel method is slower
        and may run into memory issues but is more accurate and allows
        manipulation of the image.
        N.B. many of the class methods are dependent on the voxel image.
    """

    def __init__(self, network, fibre_rad=3e-06, **kwargs):
        super().__init__(network=network, **kwargs)
        self._fibre_rad = fibre_rad
        if 'vox_len' in kwargs.keys():
            self._vox_len = kwargs['vox_len']
        else:
            self._vox_len = 1e-6
        # Set all the required models
        vertices = network.find_pore_hulls()
        p_coords = np.array([network['pore.coords'][p] for p in vertices],
                            dtype=object)
        self['pore.vertices'] = p_coords
        vertices = network.find_throat_facets()
        t_coords = np.array([network['pore.coords'][t] for t in vertices],
                            dtype=object)
        self['throat.vertices'] = t_coords
        # Once vertices are saved we no longer need the voronoi network
#        topotools.trim(network=network, pores=network.pores('voronoi'))
#        topotools.trim(network=network, throats=network.throats('voronoi'))
        self.in_hull_volume(fibre_rad=fibre_rad)
        self['throat.normal'] = self._t_normals()
        self['throat.centroid'] = self._centroids(verts=t_coords)
        self['pore.centroid'] = self._centroids(verts=p_coords)
        (self['pore.indiameter'],
         self['pore.incenter']) = self._indiameter_from_fibres()
        self._throat_props(offset=fibre_rad)
        topotools.trim_occluded_throats(network=network, mask=self.name)
        self['throat.volume'] = sp.zeros(1, dtype=float)
        self['throat.length'] = sp.ones(1, dtype=float)*self._fibre_rad*2
        self['throat.c2c'] = self._throat_c2c()
        # Configurable Models
        self.models = self.recipe()
        self.regenerate_models()

    @classmethod
    def recipe(cls):
        sf_mod = gm.throat_shape_factor.compactness
        sa_mod = gm.throat_surface_area.extrusion
        r = {'throat.shape_factor': {'model': sf_mod},
             'pore.seed': {'model': gm.pore_misc.random,
                           'num_range': [0, 0.1],
                           'seed': None},
             'throat.seed': {'model': gm.throat_misc.neighbor,
                             'pore_prop': 'pore.seed',
                             'mode': 'min'},
             'pore.diameter': {'model': gm.pore_size.equivalent_sphere},
             'pore.area': {'model': gm.pore_area.spherical,
                           'pore_diameter': 'pore.diameter'},
             'throat.surface_area': {'model': sa_mod},
             }
        return r

    def _t_normals(self):
        r"""
        Update the throat normals from the voronoi vertices
        """
        verts = self['throat.vertices']
        value = sp.zeros([len(verts), 3])
        for i in range(len(verts)):
            if len(sp.unique(verts[i][:, 0])) == 1:
                verts_2d = sp.vstack((verts[i][:, 1], verts[i][:, 2])).T
            elif len(sp.unique(verts[i][:, 1])) == 1:
                verts_2d = sp.vstack((verts[i][:, 0], verts[i][:, 2])).T
            else:
                verts_2d = sp.vstack((verts[i][:, 0], verts[i][:, 1])).T
            hull = sptl.ConvexHull(verts_2d, qhull_options='QJ Pp')
            sorted_verts = verts[i][hull.vertices]
            v1 = sorted_verts[1]-sorted_verts[0]
            v2 = sorted_verts[-1]-sorted_verts[0]
            value[i] = sp.cross(v1, v2)
        return value

    def _centroids(self, verts):
        r'''
        Function to calculate the centroid as the mean of a set of vertices.
        Used for pore and throat.
        '''
        value = sp.zeros([len(verts), 3])
        for i, i_verts in enumerate(verts):
            value[i] = np.mean(i_verts, axis=0)
        return value

    def _indiameter_from_fibres(self):
        r"""
        Calculate an indiameter by distance transforming sections of the
        fibre image. By definition the maximum value will be the largest radius
        of an inscribed sphere inside the fibrous hull
        """
        Np = self.num_pores()
        indiam = np.zeros(Np, dtype=float)
        incen = np.zeros([Np, 3], dtype=float)
        hull_pores = np.unique(self._hull_image)
        (Lx, Ly, Lz) = np.shape(self._hull_image)
        (indx, indy, indz) = np.indices([Lx, Ly, Lz])
        indx = indx.flatten()
        indy = indy.flatten()
        indz = indz.flatten()
        for i, pore in enumerate(hull_pores):
            logger.info("Processing pore: "+str(i)+" of "+str(len(hull_pores)))
            dt_pore = self._dt_image*(self._hull_image == pore)
            indiam[pore] = dt_pore.max()*2
            max_ind = np.argmax(dt_pore)
            incen[pore, 0] = indx[max_ind]
            incen[pore, 1] = indy[max_ind]
            incen[pore, 2] = indz[max_ind]
        indiam *= self._vox_len
        incen *= self._vox_len
        return (indiam, incen)

    def _throat_c2c(self):
        r"""
        Calculate the centre to centre distance from centroid of pore1 to
        centroid of throat to centroid of pore2.
        """
        net = self.network
        Nt = net.num_throats()
        p_cen = net['pore.centroid']
        t_cen = net['throat.centroid']
        conns = net['throat.conns']
        p1 = conns[:, 0]
        p2 = conns[:, 1]
        v1 = t_cen-p_cen[p1]
        v2 = t_cen-p_cen[p2]
        check_nan = ~sp.any(sp.isnan(v1 + v2), axis=1)
        value = sp.ones(Nt, dtype=float)*sp.nan
        for i in range(Nt):
            if check_nan[i]:
                value[i] = sp.linalg.norm(v1[i])+sp.linalg.norm(v2[i])
        return value[net.throats(self.name)]

    def _throat_props(self, offset):
        r"""
        Use the Voronoi vertices and perform image analysis to obtain throat
        properties
        """
        mask = self['throat.delaunay']
        Nt = len(mask)
        net_Nt = self.num_throats()
        if Nt == net_Nt:
            centroid = sp.zeros([Nt, 3])
            incentre = sp.zeros([Nt, 3])
        else:
            centroid = sp.ndarray(Nt, dtype=object)
            incentre = sp.ndarray(Nt, dtype=object)
        area = sp.zeros(Nt)
        perimeter = sp.zeros(Nt)
        inradius = sp.zeros(Nt)
        equiv_diameter = sp.zeros(Nt)
        eroded_verts = sp.ndarray(Nt, dtype=object)

        res = 200
        vertices = self['throat.vertices']
        normals = self['throat.normal']
        z_axis = [0, 0, 1]

        for i in self.throats('delaunay'):
            logger.info("Processing throat " + str(i+1)+" of "+str(Nt))
            # For boundaries some facets will already be aligned with the axis
            # if this is the case a rotation is unnecessary
            angle = tr.angle_between_vectors(normals[i], z_axis)
            if angle == 0.0 or angle == np.pi:
                # We are already aligned
                rotate_facet = False
                facet = vertices[i]
            else:
                rotate_facet = True
                M = tr.rotation_matrix(tr.angle_between_vectors(normals[i],
                                                                z_axis),
                                       tr.vector_product(normals[i], z_axis))
                facet = np.dot(vertices[i], M[:3, :3].T)
            x = facet[:, 0]
            y = facet[:, 1]
            z = facet[:, 2]
            # Get points in 2d for image analysis
            pts = np.column_stack((x, y))
            # Translate points so min sits at the origin
            translation = [pts[:, 0].min(), pts[:, 1].min()]
            pts -= translation
            order = np.int(math.ceil(-np.log10(np.max(pts))))
            # Normalise and scale the points so that largest span equals the
            # resolution to save on memory and create clear image
            max_factor = np.max([pts[:, 0].max(), pts[:, 1].max()])
            f = res/max_factor
            # Scale the offset and define a structuring element with radius
            r = f*offset
            # Only proceed if r is less than half the span of the image"
            if r <= res/2:
                pts *= f
                minp1 = pts[:, 0].min()
                minp2 = pts[:, 1].min()
                maxp1 = pts[:, 0].max()
                maxp2 = pts[:, 1].max()
                img = np.zeros([np.int(math.ceil(maxp1-minp1)+1),
                                np.int(math.ceil(maxp2-minp2)+1)])
                int_pts = np.around(pts, 0).astype(int)
                for pt in int_pts:
                    img[pt[0]][pt[1]] = 1
                # Pad with zeros all the way around the edges
                img_pad = np.zeros([np.shape(img)[0]+2, np.shape(img)[1]+2])
                img_pad[1:np.shape(img)[0]+1, 1:np.shape(img)[1]+1] = img
                # All points should lie on this plane but could be some
                # rounding errors so use the order parameter
                z_plane = sp.unique(np.around(z, order+2))
                if len(z_plane) > 1:
                    logger.error('Rotation for image analysis failed')
                    temp_arr = np.ones(1)
                    temp_arr.fill(np.mean(z_plane))
                    z_plane = temp_arr
                "Fill in the convex hull polygon"
                convhullimg = convex_hull_image(img_pad)
                # Perform a Distance Transform and black out points less than r
                # to create binary erosion. This is faster than performing an
                # erosion and dt can also be used later to find incircle
                eroded = ndimage.distance_transform_edt(convhullimg)
                eroded[eroded <= r] = 0
                eroded[eroded > r] = 1
                # If we are left with less than 3 non-zero points then the
                # throat is fully occluded
                if np.sum(eroded) >= 3:
                    # Do some image analysis to extract the key properties
                    cropped = eroded[1:np.shape(img)[0]+1,
                                     1:np.shape(img)[1]+1].astype(int)
                    regions = regionprops(cropped)
                    # Change this to cope with genuine multi-region throats
                    if len(regions) == 1:
                        for props in regions:
                            x0, y0 = props.centroid
                            equiv_diameter[i] = props.equivalent_diameter
                            area[i] = props.area
                            perimeter[i] = props.perimeter
                            coords = props.coords
                        # Undo the translation, scaling and truncation on the
                        # centroid
                        centroid2d = [x0, y0]/f
                        centroid2d += (translation)
                        centroid3d = np.concatenate((centroid2d, z_plane))
                        # Distance transform the eroded facet to find the
                        # incentre and inradius
                        dt = ndimage.distance_transform_edt(eroded)
                        temp = np.unravel_index(dt.argmax(), dt.shape)
                        inx0, iny0 = np.asarray(temp).astype(float)
                        incentre2d = [inx0, iny0]
                        # Undo the translation, scaling and truncation on the
                        # incentre
                        incentre2d /= f
                        incentre2d += (translation)
                        incentre3d = np.concatenate((incentre2d, z_plane))
                        # The offset vertices will be those in the coords that
                        # are closest to the originals
                        offset_verts = []
                        for pt in int_pts:
                            vert = np.argmin(np.sum(np.square(coords-pt),
                                                    axis=1))
                            if vert not in offset_verts:
                                offset_verts.append(vert)
                        # If we are left with less than 3 different vertices
                        # then the throat is fully occluded as we can't make a
                        # shape with non-zero area
                        if len(offset_verts) >= 3:
                            offset_coords = coords[offset_verts].astype(float)
                            # Undo the translation, scaling and truncation on
                            # the offset_verts
                            offset_coords /= f
                            offset_coords_3d = \
                                np.vstack((offset_coords[:, 0]+translation[0],
                                           offset_coords[:, 1]+translation[1],
                                           np.ones(len(offset_verts))*z_plane))
                            oc_3d = offset_coords_3d.T
                            # Get matrix to un-rotate the co-ordinates back to
                            # the original orientation if we rotated in the
                            # first place
                            if rotate_facet:
                                MI = tr.inverse_matrix(M)
                                # Unrotate the offset coordinates
                                incentre[i] = np.dot(incentre3d, MI[:3, :3].T)
                                centroid[i] = np.dot(centroid3d, MI[:3, :3].T)
                                eroded_verts[i] = np.dot(oc_3d, MI[:3, :3].T)
                            else:
                                incentre[i] = incentre3d
                                centroid[i] = centroid3d
                                eroded_verts[i] = oc_3d

                            inradius[i] = dt.max()
                            # Undo scaling on other parameters
                            area[i] /= f*f
                            perimeter[i] /= f
                            equiv_diameter[i] /= f
                            inradius[i] /= f
                        else:
                            area[i] = 0
                            perimeter[i] = 0
                            equiv_diameter[i] = 0

        self['throat.area'] = area
        self['throat.perimeter'] = perimeter
        self['throat.centroid'] = centroid
        self['throat.diameter'] = equiv_diameter
        self['throat.indiameter'] = inradius*2
        self['throat.incentre'] = incentre
        self['throat.offset_vertices'] = eroded_verts

    def inhull(self, xyz, pore, tol=1e-7):
        r"""
        Tests whether points lie within a convex hull or not.
        Computes a tesselation of the hull works out the normals of the facets.
        Then tests whether dot(x.normals) < dot(a.normals) where a is the the
        first vertex of the facets
        """
        xyz = np.around(xyz, 10)
        # Work out range to span over for pore hull
        xmin = xyz[:, 0].min()
        xr = (np.ceil(xyz[:, 0].max())-np.floor(xmin)).astype(int)+1
        ymin = xyz[:, 1].min()
        yr = (np.ceil(xyz[:, 1].max())-np.floor(ymin)).astype(int)+1
        zmin = xyz[:, 2].min()
        zr = (np.ceil(xyz[:, 2].max())-np.floor(zmin)).astype(int)+1

        origin = np.array([xmin, ymin, zmin])
        # start index
        si = np.floor(origin).astype(int)
        xyz -= origin
        dom = np.zeros([xr, yr, zr], dtype=np.uint8)
        indx, indy, indz = np.indices((xr, yr, zr))
        # Calculate the tesselation of the points
        hull = sptl.ConvexHull(xyz)
        # Assume 3d for now
        # Calc normals from the vector cross product of the vectors defined
        # by joining points in the simplices
        vab = xyz[hull.simplices[:, 0]]-xyz[hull.simplices[:, 1]]
        vac = xyz[hull.simplices[:, 0]]-xyz[hull.simplices[:, 2]]
        nrmls = np.cross(vab, vac)
        # Scale normal vectors to unit length
        nrmlen = np.sum(nrmls**2, axis=-1)**(1./2)
        nrmls = nrmls*np.tile((1/nrmlen), (3, 1)).T
        # Center of Mass
        center = np.mean(xyz, axis=0)
        # Any point from each simplex
        a = xyz[hull.simplices[:, 0]]
        # Make sure all normals point inwards
        dp = np.sum((np.tile(center, (len(a), 1))-a)*nrmls, axis=-1)
        k = dp < 0
        nrmls[k] = -nrmls[k]
        # Now we want to test whether dot(x,N) >= dot(a,N)
        aN = np.sum(nrmls*a, axis=-1)
        for plane_index in range(len(a)):
            eqx = nrmls[plane_index][0]*(indx)
            eqy = nrmls[plane_index][1]*(indy)
            eqz = nrmls[plane_index][2]*(indz)
            xN = eqx + eqy + eqz
            dom[xN - aN[plane_index] >= 0-tol] += 1
        dom[dom < len(a)] = 0
        dom[dom == len(a)] = 1
        ds = np.shape(dom)
        temp_arr = np.zeros_like(self._hull_image, dtype=bool)
        temp_arr[si[0]:si[0]+ds[0], si[1]:si[1]+ds[1], si[2]:si[2]+ds[2]] = dom
        self._hull_image[temp_arr] = pore
        del temp_arr

    def in_hull_volume(self, fibre_rad=5e-6):
        r"""
        Work out the voxels inside the convex hull of the voronoi vertices of
        each pore
        """
        Ps = self.network.pores(['internal', 'delaunay'], mode='intersection')
        inds = self.network._map(ids=self['pore._id'][Ps], element='pore',
                                 filtered=True)
        # Voxel volume
        vox_len = self._vox_len
        voxel = vox_len**3
        # Voxel length of fibre radius
        fibre_rad = np.around((fibre_rad-(vox_len/2))/vox_len, 0).astype(int)
        # Get the fibre image
        self._get_fibre_image(inds, vox_len, fibre_rad)
        hull_image = np.ones_like(self._fibre_image, dtype=np.uint16)*-1
        self._hull_image = hull_image
        for pore in Ps:
            logger.info("Processing Pore: "+str(pore+1)+" of "+str(len(Ps)))
            verts = self['pore.vertices'][pore]
            verts = np.asarray(unique_list(np.around(verts, 6)))
            verts /= vox_len
            self.inhull(verts, pore)
        self._process_pore_voxels()
        self['pore.volume'] = self['pore.pore_voxels']*voxel

    def _process_pore_voxels(self):
        r'''
        Function to count the number of voxels in the pore and fibre space
        Which are assigned to each hull volume
        '''
        num_Ps = self.num_pores()
        pore_vox = sp.zeros(num_Ps, dtype=int)
        fibre_vox = sp.zeros(num_Ps, dtype=int)
        pore_space = self._hull_image.copy()
        fibre_space = self._hull_image.copy()
        pore_space[self._fibre_image == 0] = -1
        fibre_space[self._fibre_image == 1] = -1
        freq_pore_vox = itemfreq(pore_space)
        freq_pore_vox = freq_pore_vox[freq_pore_vox[:, 0] > -1]
        freq_fibre_vox = itemfreq(fibre_space)
        freq_fibre_vox = freq_fibre_vox[freq_fibre_vox[:, 0] > -1]
        pore_vox[freq_pore_vox[:, 0]] = freq_pore_vox[:, 1]
        fibre_vox[freq_fibre_vox[:, 0]] = freq_fibre_vox[:, 1]
        self['pore.fibre_voxels'] = fibre_vox
        self['pore.pore_voxels'] = pore_vox
        del pore_space
        del fibre_space

    def _bresenham(self, faces, dx):
        line_points = []
        for face in faces:
            # Get in hull order
            fx = face[:, 0]
            fy = face[:, 1]
            fz = face[:, 2]
            # Find the axis with the smallest spread and remove it to make 2D
            if (np.std(fx) < np.std(fy)) and (np.std(fx) < np.std(fz)):
                f2d = np.vstack((fy, fz)).T
            elif (np.std(fy) < np.std(fx)) and (np.std(fy) < np.std(fz)):
                f2d = np.vstack((fx, fz)).T
            else:
                f2d = np.vstack((fx, fy)).T
            hull = sptl.ConvexHull(f2d, qhull_options='QJ Pp')
            face = np.around(face[hull.vertices], 6)
            for i in range(len(face)):
                vec = face[i]-face[i-1]
                vec_length = np.linalg.norm(vec)
                increments = np.ceil(vec_length/dx)
                check_p_old = np.array([-1, -1, -1])
                for x in np.linspace(0, 1, increments):
                    check_p_new = face[i-1]+(vec*x)
                    if np.sum(check_p_new - check_p_old) != 0:
                        line_points.append(check_p_new)
                        check_p_old = check_p_new
        return np.asarray(line_points)

    def _get_fibre_image(self, cpores, vox_len, fibre_rad):
        r"""
        Produce image by filling in voxels along throat edges using Bresenham
        line then performing distance transform on fibre voxels to erode the
        pore space
        """
        net = self.network
        verts = self['throat.vertices']
        [vxmin, vxmax, vymin,
         vymax, vzmin, vzmax] = vo.vertex_dimension(net,
                                                    net.pores('delaunay'),
                                                    parm='minmax')
        # Translate vertices so that minimum occurs at the origin
        for index in range(len(verts)):
            verts[index] -= np.array([vxmin, vymin, vzmin])
        # Find new size of image array
        cdomain = np.around(np.array([(vxmax-vxmin),
                                      (vymax-vymin),
                                      (vzmax-vzmin)]), 6)
        logger.info("Creating fibres in range: " + str(np.around(cdomain, 5)))
        lx = np.int(np.around(cdomain[0]/vox_len)+1)
        ly = np.int(np.around(cdomain[1]/vox_len)+1)
        lz = np.int(np.around(cdomain[2]/vox_len)+1)
        # Try to create all the arrays we will need at total domain size
        try:
            pore_space = np.ones([lx, ly, lz], dtype=np.uint8)
            fibre_space = np.zeros(shape=[lx, ly, lz], dtype=np.uint8)
            dt = np.zeros([lx, ly, lz], dtype=float)
            # Only need one chunk
            cx = cy = cz = 1
            chunk_len = np.max(np.shape(pore_space))
        except:
            logger.info("Domain too large to fit into memory so chunking " +
                        "domain to process image, this may take some time")
            # Do chunking
            chunk_len = 100
            if (lx > chunk_len):
                cx = np.ceil(lx/chunk_len).astype(int)
            else:
                cx = 1
            if (ly > chunk_len):
                cy = np.ceil(ly/chunk_len).astype(int)
            else:
                cy = 1
            if (lz > chunk_len):
                cz = np.ceil(lz/chunk_len).astype(int)
            else:
                cz = 1

        # Get image of the fibres
        line_points = self._bresenham(verts, vox_len/2)
        line_ints = (np.around((line_points/vox_len), 0)).astype(int)
        for x, y, z in line_ints:
            try:
                pore_space[x][y][z] = 0
            except IndexError:
                logger.warning("Some elements in image processing are out" +
                               "of bounds")

        num_chunks = np.int(cx*cy*cz)
        cnum = 1
        for ci in range(cx):
            for cj in range(cy):
                for ck in range(cz):
                    # Work out chunk range
                    logger.info("Processing Fibre Chunk: "+str(cnum)+" of " +
                                str(num_chunks))
                    cxmin = ci*chunk_len
                    cxmax = np.int(np.ceil((ci+1)*chunk_len + 5*fibre_rad))
                    cymin = cj*chunk_len
                    cymax = np.int(np.ceil((cj+1)*chunk_len + 5*fibre_rad))
                    czmin = ck*chunk_len
                    czmax = np.int(np.ceil((ck+1)*chunk_len + 5*fibre_rad))
                    # Don't overshoot
                    if cxmax > lx:
                        cxmax = lx
                    if cymax > ly:
                        cymax = ly
                    if czmax > lz:
                        czmax = lz
                    dt_edt = ndimage.distance_transform_edt
                    dtc = dt_edt(pore_space[cxmin:cxmax,
                                            cymin:cymax,
                                            czmin:czmax])
                    fibre_space[cxmin:cxmax,
                                cymin:cymax,
                                czmin:czmax][dtc <= fibre_rad] = 0
                    fibre_space[cxmin:cxmax,
                                cymin:cymax,
                                czmin:czmax][dtc > fibre_rad] = 1
                    dt[cxmin:cxmax,
                       cymin:cymax,
                       czmin:czmax] = dtc - fibre_rad
                    cnum += 1
        del pore_space
        self._fibre_image = fibre_space
        dt[dt < 0] = 0
        self._dt_image = dt

    def _get_fibre_slice(self, plane=None, index=None):
        r"""
        Plot an image of a slice through the fibre image
        plane contains percentage values of the length of the image in each
        axis

        Parameters
        ----------
        plane : array_like
        List of 3 values, [x,y,z], 2 must be zero and the other must be between
        zero and one representing the fraction of the domain to slice along
        the non-zero axis

        index : array_like
        similar to plane but instead of the fraction an index of the image is
        used
        """
        if hasattr(self, '_fibre_image') is False:
            logger.warning('This method only works when a fibre image exists')
            return None
        if plane is None and index is None:
            logger.warning('Please provide a plane array or index array')
            return None

        if plane is not None:
            if 'array' not in plane.__class__.__name__:
                plane = sp.asarray(plane)
            if sp.sum(plane == 0) != 2:
                logger.warning('Plane argument must have two zero valued ' +
                               'elements to produce a planar slice')
                return None
            l = sp.asarray(sp.shape(self._fibre_image))
            s = sp.around(plane*l).astype(int)
        elif index is not None:
            if 'array' not in index.__class__.__name__:
                index = sp.asarray(index)
            if sp.sum(index == 0) != 2:
                logger.warning('Index argument must have two zero valued ' +
                               'elements to produce a planar slice')
                return None
            if 'int' not in str(index.dtype):
                index = sp.around(index).astype(int)
            s = index

        if s[0] != 0:
            slice_image = self._fibre_image[s[0], :, :]
        elif s[1] != 0:
            slice_image = self._fibre_image[:, s[1], :]
        else:
            slice_image = self._fibre_image[:, :, s[2]]

        return slice_image

    def plot_fibre_slice(self, plane=None, index=None, fig=None):
        r"""
        Plot one slice from the fibre image

        Parameters
        ----------
        plane : array_like
        List of 3 values, [x,y,z], 2 must be zero and the other must be between
        zero and one representing the fraction of the domain to slice along
        the non-zero axis

        index : array_like
        similar to plane but instead of the fraction an index of the image is
        used
        """
        if hasattr(self, '_fibre_image') is False:
            logger.warning('This method only works when a fibre image exists')
            return
        slice_image = self._get_fibre_slice(plane, index)
        if slice_image is not None:
            if fig is None:
                plt.figure()
            plt.imshow(slice_image.T, cmap='Greys', origin='lower',
                       interpolation='nearest')

        return fig

    def plot_porosity_profile(self, fig=None):
        r"""
        Return a porosity profile in all orthogonal directions by summing
        the voxel volumes in consectutive slices.
        """
        if hasattr(self, '_fibre_image') is False:
            logger.warning('This method only works when a fibre image exists')
            return

        l = sp.asarray(sp.shape(self._fibre_image))
        px = sp.zeros(l[0])
        py = sp.zeros(l[1])
        pz = sp.zeros(l[2])

        for x in sp.arange(l[0]):
            px[x] = sp.sum(self._fibre_image[x, :, :])
            px[x] /= sp.size(self._fibre_image[x, :, :])
        for y in sp.arange(l[1]):
            py[y] = sp.sum(self._fibre_image[:, y, :])
            py[y] /= sp.size(self._fibre_image[:, y, :])
        for z in sp.arange(l[2]):
            pz[z] = sp.sum(self._fibre_image[:, :, z])
            pz[z] /= sp.size(self._fibre_image[:, :, z])

        if fig is None:
            fig = plt.figure()
        ax = fig.gca()
        plots = []
        plots.append(plt.plot(sp.arange(l[0])/l[0], px, 'r', label='x'))
        plots.append(plt.plot(sp.arange(l[1])/l[1], py, 'g', label='y'))
        plots.append(plt.plot(sp.arange(l[2])/l[2], pz, 'b', label='z'))
        plt.xlabel('Normalized Distance')
        plt.ylabel('Porosity')
        handles, labels = ax.get_legend_handles_labels()
        ax.legend(handles, labels, loc=1)
        plt.legend(bbox_to_anchor=(1, 1), loc=2, borderaxespad=0.)
        return fig
