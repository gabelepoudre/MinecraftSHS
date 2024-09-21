import requests
import re

# looking for, want to get the link
"""
</label>
<a href="https://minecraft.azureedge.net/bin-win/bedrock-server-1.21.30.03.zip" 
aria-label="Download Minecraft Dedicated Server software for Windows" class="btn btn-disabled-outline mt-4 downloadlink" role="button" data-platform="serverBedrockWindows" tabindex="0" aria-disabled="true">Download </a>
</div>
"""

_compiled = re.compile(r'href="https://minecraft.azureedge.net/bin-win/bedrock-server-([0-9.]+).zip"')


def get_latest_version():
    # get with short timeout, it seems like it goes down frequently (or is heavily rate limited)
    try:
        r = requests.get(
            "https://www.minecraft.net/en-us/download/server/bedrock",
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.google.com"
            }
        )
    except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout):
        return None
    if r.status_code != 200:
        return None
    matches = _compiled.findall(r.text)
    if not matches:
        return None
    return matches[0]


if __name__ == "__main__":
    print(get_latest_version())
