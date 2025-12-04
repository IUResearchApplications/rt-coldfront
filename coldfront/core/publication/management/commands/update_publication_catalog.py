import datetime
import json
import pathlib
import urllib.request
from argparse import ArgumentParser
from urllib.parse import quote
from xml.etree import ElementTree

import feedparser
import pymupdf
import requests
from django.core.management.base import BaseCommand

from coldfront.core.publication.models import Publication


class Command(BaseCommand):
    help = "Updates the publication catalog by fetching latest citation information from Crossref for each publication."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--process_pdfs",
            action="store_true",
            help="If images of the first page of the pdfs for each citation should be saved.",
        )
        parser.add_argument(
            "--update",
            action="store_true",
            help="If existing citations should be replaced. Set --date if only citations before a certain date should be.",
        )
        parser.add_argument(
            "--date",
            type=str,
            default=datetime.date.today().isoformat(),
            help="Only used if --update is enabled. All citations created before this date will be replaced.",
        )
        parser.add_argument(
            "--dir",
            type=str,
            default=".",
            help="Where all the files are stored/saved.",
        )

    def handle(self, *args, **kwargs) -> None:
        process_pdfs = kwargs.get("process_pdfs")
        update = kwargs.get("update")
        date = kwargs.get("date")
        date = datetime.datetime.strptime(date, "%Y-%m-%d")
        dir = kwargs.get("dir")

        publications = set(
            Publication.objects.filter().exclude(source__name="manual").values_list("id", "unique_id", "title")
        )

        dir = pathlib.Path(dir)
        cache_file = dir / "catalog_cache.json"
        cache_file.touch()
        with cache_file.open() as cf:
            try:
                cache = json.load(cf)
            except json.decoder.JSONDecodeError:
                cache = {}

        cache = self.get_citations(publications, cache, update, date)
        cache = self.get_screenshots(publications, cache, process_pdfs, dir)
        self.save_files(cache, cache_file, dir)

    def save_files(self, cache: dict, cache_file: pathlib.Path, dir: pathlib.Path) -> None:
        """Saves the updated citation information along with these files to the specified directory:
        * apa.text: Conatins rows of citations in alphabetical order.
        * links.txt: Contains rows of pub_id and DOI links.
        * missing_pdfs: Contains rows of pub_id and DOI links for PDFs that were not able to be downloaded.

        Parameters:
            cache (dact): The updated citation information in the form {pub_id: {'citation': citation, 'auto_png': auto_png, 'created': created}, ...}.
            cache_file (pathlib.Path): The path to the cache file where the updated citation information will be saved.
            dir (pathlib.Path): The path to the directory where the files will be saved in.
        """
        with cache_file.open("w") as json_cache:
            json.dump(cache, json_cache, indent=4)

        with (dir / "apa.txt").open("w") as apa:
            apa.writelines(sorted([values.get("citation") for values in cache.values()]))

        with (dir / "links.txt").open("w") as missing_pdfs:
            missing_pdfs.writelines(
                f"{id} {values.get('citation').rsplit(' ', 1)[-1]}"
                for id, values in cache.items()
                if values.get("auto_png")
            )

        with (dir / "missing_pdfs.txt").open("w") as missing_pdfs:
            missing_pdfs.writelines(
                f"{id} {values.get('citation').rsplit(' ', 1)[-1]}"
                for id, values in cache.items()
                if not values.get("auto_png")
            )

    def get_citations(self, publications: set, cache: dict, update: bool, date: str) -> dict:
        """Fetches latest citation information from Crossref for each publication.

        Parameters:
            publications (set): Publications in the form of [(pub_id, unique_id, title), ...].
            cache (dict): Previously fetched citation information in the form {pub_id: {'citation': citation, 'auto_png': auto_png, 'created': created}, ...}.
            update (bool): If set to True, the command will update existing citations based on the date parameter. If set to False, the command will only add new citations.
            date (str): The date before which citations are updated. This parameter is only used if update is set to True.

        Returns:
           Updated citation information in the form {pub_id: {'citation': citation, 'auto_png': auto_png, 'created': created}, ...}.
        """
        todays_date = datetime.date.today().isoformat()
        for pub_id, unique_id, _ in publications:
            cached_citation = cache.get(str(pub_id))
            if cached_citation:
                if not update:
                    continue
                if datetime.datetime.strptime(cached_citation.get("created"), "%Y-%m-%d") > date:
                    continue

            valid, citation = self.get_citation(unique_id)
            if valid:
                cache[str(pub_id)] = {
                    "citation": self.fix_formatting(citation),
                    "auto_png": False,
                    "has_png": False,
                    "created": todays_date,
                }

        return cache

    def get_citation(self, doi_number: str) -> tuple:
        """Fetches the citation information from Crossref for a given publication.

        Parameters:
            doi_number (str): The DOI number of the publication.

        Returns:
            A boolean value indicating whether the citation was found and the citation itself.
        """
        api_url = f"https://api.crossref.org/works/{doi_number}/transform/text/x-bibliography"
        req = requests.get(api_url)
        valid = req.status_code == 200
        citation = str(req.content, encoding="utf-8")

        return valid, citation

    def get_screenshots(self, publications: set, cache: dict, process_pdfs: bool, dir: str) -> dict:
        """Fetches the first page of the PDF for each publication and saves it as an image file.

        Parameters:
            publications (set): Publications in the form [(pub_id, unique_id, title), ...].
            cache (dict): Previously fetched citation information in the form {pub_id: {'citation': citation, 'auto_png': auto_png, 'created': created}, ...}.
            process_pdfs (bool): If set to True, the command will fetch the first page of each PDF for each citation.
            dir (str): The directory where the image files will be saved.

        Returns:
            The updated citation information in the form {pub_id: {'citation': citation, 'auto_png': auto_png, 'created': created}, ...}.
        """
        for pub_id, unique_id, title in publications:
            if cache.get(str(pub_id), {}).get("auto_png"):
                continue
            if process_pdfs:
                valid = self.get_screenshot(title, unique_id, pub_id, dir)
                cache[str(pub_id)]["auto_png"] = valid
                cache[str(pub_id)]["has_png"] = valid

        return cache

    def get_screenshot(self, title: str, unique_id: str, pub_id: int, dir: str) -> bool:
        """Fetches the first page of the PDF for a given publication and saves it as an image file.

        Parameters:
            title (str): The title of the publication.
            unique_id (str): The DOI ID of the publication.
            pub_id (int): The ID of the publication.
            dir (dir): The directory where the image file will be saved.
        Returns:
            A boolean value indicating whether the image file was successfully saved.
        """
        png_file = dir / "images" / f"{pub_id}.png"
        if png_file.exists():
            return True

        parser = feedparser.parse(f"http://export.arxiv.org/api/query?search_query=ti:{quote(title)}")
        for entry in parser.get("entries"):
            if entry.get("arxiv_doi") == unique_id:
                for link in entry.get("links"):
                    if link.get("title") == "pdf":
                        path, _ = urllib.request.urlretrieve(link.get("href"))
                        doc = pymupdf.open(path)
                        doc[0].get_pixmap().save(png_file)
                        return True
        return False

    def fix_formatting(self, citation: str) -> str:
        """Formats the citation to remove any mml tags.

        Parameters:
            citation (str): The citation.
        Returns:
            Formatted citation.

        """
        mathml_start = citation.find("<mml:math")
        while mathml_start >= 0:
            mathml_end = citation.find("</mml:math>") + len("</mml:math>")
            root = ElementTree.fromstring(citation[mathml_start:mathml_end])
            combined_text = self.get_text_from_xml_ele(root, "")
            citation = " ".join([citation[:mathml_start], combined_text, citation[mathml_end:]])
            mathml_start = citation.find("<mml:math")
        return citation

    def get_text_from_xml_ele(self, ele: ElementTree.Element, text: str) -> str:
        """Extracts the text from an XML element.

        Parameters:
            ele (ElementTree.Element): The XML element.
            text (str): The previous extracted text.
        Returns:
            Extracted text.
        """
        if ele.text is None:
            sub_text = ""
            for sub_ele in ele:
                sub_text += self.get_text_from_xml_ele(sub_ele, text)
            return sub_text
        return text + ele.text
