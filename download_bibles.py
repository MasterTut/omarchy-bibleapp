import os
import urllib.request
import re

def download_bible(name, url, headers):
    print(f"Downloading {name} from {url}...")
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as response:
            with open(f"translations/{name}.epub", "wb") as f:
                f.write(response.read())
        print(f"Successfully downloaded {name}.")
    except Exception as e:
        print(f"Failed to download {name}: {e}")

if __name__ == "__main__":
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    try:
        req = urllib.request.Request("https://ereaderbibles.com/", headers=headers)
        with urllib.request.urlopen(req) as response:
            html = response.read().decode('utf-8')
            
            links = re.findall(r'href=["\']([^"\']+\.epub)["\']', html)
            
            found_asv = False
            found_kjv = False
            
            for link in links:
                l_lower = link.lower()
                
                if ("asv" in l_lower or "american-standard" in l_lower) and not found_asv:
                    full_url = link if link.startswith("http") else "https://ereaderbibles.com/" + link.lstrip("/")
                    download_bible("ASV", full_url, headers)
                    found_asv = True
                    
                if ("kjv" in l_lower or "king-james" in l_lower) and not found_kjv:
                    full_url = link if link.startswith("http") else "https://ereaderbibles.com/" + link.lstrip("/")
                    download_bible("KJV", full_url, headers)
                    found_kjv = True
            
            if not found_asv:
                print("Could not find ASV link.")
            if not found_kjv:
                print("Could not find KJV link.")

    except Exception as e:
        print(f"Error fetching site: {e}")
