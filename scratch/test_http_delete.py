import urllib.request
import json

url = "http://127.0.0.1:5000/map/delete"
data = {"name": "dock1.pgm"}
req = urllib.request.Request(
    url, 
    data=json.dumps(data).encode('utf-8'),
    headers={'Content-Type': 'application/json'}
)

try:
    with urllib.request.urlopen(req) as response:
        html = response.read().decode('utf-8')
        print("Success response:")
        print(html)
except urllib.error.HTTPError as e:
    print("HTTP Error:", e.code)
    print(e.read().decode('utf-8'))
except Exception as e:
    print("Error:", e)
