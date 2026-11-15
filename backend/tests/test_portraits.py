"""Portrait selection: the page must name the person and the file must carry their surname."""
from polgraph.portraits import find_portraits, pick_portrait, portrait_url

WIKI = "https://es.wikipedia.org/wiki/Clara_Brugada"
PHOTO = ("https://thumb.wikimedia.org/wikipedia/commons/thumb/9/91/Clara_Brugada_Molina_%28cropped%29.jpg/"
         "250px-Clara_Brugada_Molina_%28cropped%29.jpg?utm_source=es.wikipedia.org")
ARMS = "https://upload.wikimedia.org/wikipedia/commons/thumb/2/21/Coat_of_arms_of_Mexico_City.svg/40px-x.png"


def test_pick_portrait_requires_named_page_and_surname_file():
    results = [
        {"url": "https://es.wikipedia.org/wiki/Jefe_de_Gobierno", "title": "Jefe de Gobierno de Ciudad de México",
         "image_links": [PHOTO]},  # page is not about her
        {"url": WIKI, "title": "Clara Brugada", "image_links": [ARMS, PHOTO]},
    ]
    hit = pick_portrait("Clara Marina Brugada Molina", results)
    assert hit["image_source_url"] == WIKI and hit["image_source_domain"] == "es.wikipedia.org"
    assert hit["image_url"] == ("https://upload.wikimedia.org/wikipedia/commons/thumb/9/91/"
                                "Clara_Brugada_Molina_%28cropped%29.jpg/330px-Clara_Brugada_Molina_%28cropped%29.jpg")
    assert pick_portrait("Clara Marina Brugada Molina", [{"url": WIKI, "title": "Clara Brugada",
                                                          "image_links": [ARMS]}]) is None
    assert portrait_url("https://example.gob.mx/a.jpg?x=1") == "https://example.gob.mx/a.jpg?x=1"


class _GW:
    def __init__(self, by_domain):
        self.by_domain, self.calls = by_domain, []

    def search(self, query, *, include_domains, **kw):
        self.calls.append((query, tuple(include_domains)))
        return self.by_domain.get(include_domains[0], [])


def test_find_portraits_stores_result_and_falls_back_to_official(store):
    store.upsert_node("person:MX:clara-marina-brugada-molina", "Person", {"name": "Clara Marina Brugada Molina"})
    store.upsert_node("person:MX:alfredo-ramirez-bedolla", "Person", {"name": "Alfredo Ramírez Bedolla"})
    store.upsert_node("person:MX:nobody-known", "Person", {"name": "Nadie Conocido"})
    gw = _GW({"es.wikipedia.org": [{"url": WIKI, "title": "Clara Brugada", "image_links": [PHOTO]}],
              "gob.mx": [{"url": "https://michoacan.gob.mx/alfredo-ramirez-bedolla/", "title": "Alfredo Ramírez Bedolla",
                          "image_links": ["https://michoacan.gob.mx/logo.svg",
                                          "https://michoacan.gob.mx/uploads/alfredo-ramirez-bedolla-300x300.jpg"]}]})
    res = find_portraits(gw, store, "mx")
    assert res == {"country": "MX", "checked": 3, "found": 2, "not_found": 1}
    clara = store.get_node("person:MX:clara-marina-brugada-molina")
    assert clara["image_status"] == "found" and clara["image_method"] == "wikipedia"
    alfredo = store.get_node("person:MX:alfredo-ramirez-bedolla")
    assert alfredo["image_method"] == "official_page" and alfredo["image_url"].endswith("-300x300.jpg")
    assert store.get_node("person:MX:nobody-known")["image_status"] == "not_found"
    calls = len(gw.calls)
    assert find_portraits(gw, store, "MX", force=True, workers=4) == {"country": "MX", "checked": 3, "found": 2,
                                                                      "not_found": 1}
    calls = len(gw.calls)
    assert find_portraits(gw, store, "MX")["checked"] == 0 and len(gw.calls) == calls  # already checked
