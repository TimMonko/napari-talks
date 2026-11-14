import napari
from skimage import data

viewer = napari.Viewer(ndisplay=3)
cells = data.cells3d()[:, 1]
viewer.add_image(cells, colormap="magma")
viewer.add_points(ndim=3, name="nuclei")
napari.run()
