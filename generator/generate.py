import bottle
import bs4
import contextlib
import dataclasses
import hashlib
import httpx
import json
import os
import re
import shutil
import time


EXTENSION_URL_TPL = "https://marketplace.visualstudio.com/items?itemName={id}"
ANALYTICS_DATA_URL = (
    "https://embedded-development-in-vscode.github.io/overview/data/analytics.json"
)


@dataclasses.dataclass
class ExtensionData:
    id: str
    publisher_title: str
    name: str
    description: str
    released_on: str
    updated_on: str
    install_cnt: int
    rating_cnt: int
    average_rating: float = None
    icon_url: str = None

    @property
    def public_id(self):
        return hashlib.sha1(self.id.lower().encode()).hexdigest()[0:3]


def list_extension_ids():
    with open("extensions.txt") as fp:
        return [
            line.strip()
            for line in fp.readlines()
            if line.strip() and not line.startswith("#")
        ]


def parse_number(text):
    match = re.match(r"^[^\d]*([\d,]+)[^\d]*$", text)
    return int(match.group(1).replace(",", "")) if match else None


def parse_average_rating(text):
    match = re.match(r"^Average rating: ([\d\.]+) out of 5$", text.strip())
    return float(match.group(1)) if match else None


def extract_extension_data(idx, html):
    soup = bs4.BeautifulSoup(html, "html.parser")
    metadata = json.loads(
        soup.find(
            "script", attrs={"class": "jiContent", "type": "application/json"}
        ).text
    )
    data = ExtensionData(
        idx,
        metadata["MoreInfo"]["PublisherValue"],
        soup.h1.text,
        soup.find("div", {"class": "ux-item-shortdesc"}).text,
        metadata["ReleaseDateString"],
        metadata["LastUpdatedDateString"],
        parse_number(soup.find("span", {"class": "installs-text"}).text) or 0,
        parse_number(soup.find("span", {"class": "ux-item-rating-count"}).text) or 0,
        parse_average_rating(
            soup.find("span", {"class": "ux-item-review-rating"}).attrs["title"]
        ),
        soup.find("td", {"class": "item-img"}).img.attrs["src"],
    )
    return data


def extract_extensions_data():
    extension_ids = list_extension_ids()
    assert extension_ids
    result = []
    with httpx.Client() as http:
        for idx in extension_ids:
            response = http.get(EXTENSION_URL_TPL.format(id=idx))
            response.raise_for_status()
            result.append(extract_extension_data(idx, response.text))
    return result


def save_icons(items):
    icons_dir = os.path.join("media", "icons")
    if not os.path.isdir(icons_dir):
        os.makedirs(icons_dir)
    for data in items:
        if not data["icon_url"]:
            continue
        with (
            httpx.stream("GET", data["icon_url"]) as stream,
            open(os.path.join(icons_dir, f"{data.get('id')}.png"), "wb+") as fp,
        ):
            for data in stream.iter_bytes():
                fp.write(data)


def humanize_number(number):
    exponents = [3, 6, 9, 12, 15]
    human = ["K", "M", "B", "T"]
    result = str(number)
    for index, exponent in enumerate(exponents):
        if number >= 10**exponent:
            result = "{0:.1f}{1}".format(number / 10**exponent, human[index])
    return result


def generate_website(extensions, dst_dir):
    with contextlib.chdir("generator/templates"):
        tpl = bottle.SimpleTemplate(name="index.tpl")
        html = tpl.render(
            EXTENSION_URL_TPL=EXTENSION_URL_TPL,
            extensions=extensions,
            humanize_number_fn=humanize_number,
        )
    with open(os.path.join(dst_dir, "index.html"), "w+") as fp:
        fp.write(html)


def generate_analytics_data(extensions, dst_dir):
    with httpx.Client() as http:
        data = http.get(ANALYTICS_DATA_URL).json()

    del [data[-1]]

    if os.environ.get("GITHUB_EVENT_NAME") == "schedule":
        data.append(
            {
                "timestamp": time.time(),
                "extensions": [
                    {
                        "pid": extension.public_id,
                        "icnt": extension.install_cnt,
                        "rcnt": extension.rating_cnt,
                        "ar": extension.average_rating,
                    }
                    for extension in extensions
                ],
            }
        )
    with open(os.path.join(dst_dir, "data", "analytics.json"), "w+") as fp:
        json.dump(data, fp)


def main():
    # cleanup
    dst_dir = "dist"
    if os.path.exists(dst_dir):
        shutil.rmtree(dst_dir)
    shutil.copytree("public", "dist")

    # fetch new data
    extensions = extract_extensions_data()
    generate_website(extensions, "dist")
    generate_analytics_data(extensions, "dist")


if __name__ == "__main__":
    main()
