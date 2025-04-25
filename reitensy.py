import time
import requests

url = "https://api.mexc.com/api/v3/ping"
start_time = time.time()
response = requests.get(url)
latency = time.time() - start_time
print(f"Latency: {latency * 1000:.2f} ms")