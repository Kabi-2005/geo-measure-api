import shutil
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Polygon

poly = Polygon([(77.00, 13.00), (77.01, 13.00),
               (77.01, 13.01), (77.00, 13.01)])
gdf = gpd.GeoDataFrame({"name": ["plot"]}, geometry=[poly], crs="EPSG:4326")

tmp = Path(tempfile.mkdtemp())
gdf.to_file(tmp / "plot.shp")

Path("samples").mkdir(exist_ok=True)
with zipfile.ZipFile("samples/plot_shapefile.zip", "w") as zf:
    for p in tmp.iterdir():
        zf.write(p, p.name)
shutil.rmtree(tmp)
print("Created samples/plot_shapefile.zip")
