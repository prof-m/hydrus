# Example given by hydev on how to decode the update files - they're actually just gzipped json files!

import zlib
import json
obj_bytes = zlib.decompress( update_file_in_bytes )
obj_string = str( obj_bytes, 'utf-8' )
obj = json.loads( obj_string )