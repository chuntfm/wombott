import random

import nltk
from nltk.tokenize import word_tokenize

from routines.fortunes import fortunecookie

# ensure nltk data is available
try:
    nltk.data.find("tokenizers/punkt_tab")
except LookupError:
    nltk.download("punkt_tab", quiet=True)
try:
    nltk.data.find("taggers/averaged_perceptron_tagger_eng")
except LookupError:
    nltk.download("averaged_perceptron_tagger_eng", quiet=True)


def chuntify(sentence: str) -> str:
    """Replace verbs with 'chunted', a noun with 'chunt', a plural noun with 'chunts'."""
    tokens = word_tokenize(sentence)
    tagged = [[token, tag] for (token, tag) in nltk.pos_tag(tokens)]

    nn_idx = []
    nns_idx = []
    for i, (token, tag) in enumerate(tagged):
        if tag == "VBD":
            tagged[i][0] = "chunted"
        elif tag == "NN":
            nn_idx.append(i)
        elif tag == "NNS":
            nns_idx.append(i)

    if nn_idx:
        tagged[random.choice(nn_idx)][0] = "chunt"
    if nns_idx:
        tagged[random.choice(nns_idx)][0] = "chunts"

    return " ".join(token for token, tag in tagged).replace(".", "").lower()


def random_chunted_fortune() -> str:
    sentence = random.choice(fortunecookie)
    return chuntify(sentence)
