import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

MAP_FOLDER = os.path.join(BASE_DIR, "maps")

print("BASE_DIR:", BASE_DIR)
print("MAP_FOLDER:", MAP_FOLDER)

# Let's test importing get_zone_handler
try:
    from zone_handler import get_zone_handler
    zh = get_zone_handler()
    print("Zone handler loaded successfully:", zh)
    if zh:
        print("get_zones_file:", zh.get_zones_file("svr123"))
        print("get_mask_file:", zh.get_mask_file("svr123", "keepout"))
except Exception as e:
    import traceback
    traceback.print_exc()
