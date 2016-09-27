import os, sys, re
from whoosh import highlight, index
from whoosh.qparser import QueryParser
from CommonMark import commonmark
from whoosh.query.qcore import _NullQuery
import argparse
from bs4 import BeautifulSoup

from my_whoosh import ParagraphFragmenter, ConsistentFragmentScorer
from books import Books
import my_index

from flask import Flask, request, render_template
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

app = Flask(__name__)
limiter = Limiter(
    app,
    key_func=get_remote_address,
    global_limits=["15 per minute", "100 per hour", "1000 per day"]
)
session_limit = 7
paragraph_limit = 3


@app.template_filter('link_abbr')
def link_abbr(abbr):
    return """<a href="#" onclick="filterBook('{0}')">{0}</a>""".format(abbr)


@app.route('/')
def search_form():
    return render_template("search-form.html", books=Books.indexed, session_limit=session_limit, paragraph_limit=paragraph_limit)


@app.route('/', methods=['POST'])
def search_form_post():
    input = request.form['query']

    with ix.searcher() as searcher:
        input = re.sub(r'\bbook:(\w+)', lambda m: m.group(0).lower(), input)
        query = QueryParser('content', ix.schema).parse(input)
        if isinstance(query, _NullQuery):
            return render_template("search-form.html", books=Books.indexed, session_limit=session_limit, paragraph_limit=paragraph_limit)

        if 'content:' in str(query):
            results = searcher.search(query, limit=session_limit)
        else:
            results = searcher.search(query, limit=150)

        results.fragmenter = ParagraphFragmenter()
        results.order = highlight.SCORE
        results.scorer = ConsistentFragmentScorer()
        results.formatter = highlight.HtmlFormatter(between='')

        output = ["<h2>Results for {}</h2>".format(query)]

        for h_idx, hit in enumerate(results):
            highlights = hit.highlights('content', top=paragraph_limit)

            output.append("""<details>
<span style="font-size: 0.9em">- {0[book_name]}<br />{0[long]}</span>
<summary>{0[book_abbr]} {0[short]}<a href="{0[book_url]}" target="_blank"><img src="/static/{0[book_abbr]}.png" style="vertical-align: text-bottom; height: 1.5em; padding: 0em 0.5em;"/></a></summary>
</details>""".format(hit))

            if not highlights:
                output.append("<br />")
                continue

            output.append("<ul>")

            for p_idx, cm_paragraph in enumerate(filter(None, highlights.split('\n'))):
                paragraph = commonmark(cm_paragraph)
                if h_idx == 0:
                    output.append("<li><p>{}</p></li>".format(paragraph))
                else:
                    sentences = get_sentence_fragments(paragraph)
                    output.append("<li><p>{}</p></li>".format(' <span class="omission">[...]</span> '.join(sentences)))

            output.append("</ul><br />")
        result = '\n'.join(output)

        return render_template("search-form.html", books=Books.indexed, query=input, result=result, session_limit=session_limit, paragraph_limit=paragraph_limit)


def get_sentence_fragments(paragraph):
    bs_paragraph = BeautifulSoup(paragraph, "lxml")

    fragments = []
    sentence_split = filter(None, re.split(r'(.*?[\.\?!][\s$])', paragraph))
    last_match_idx = None
    for s_idx, sentence in enumerate(sentence_split):
        sentence = sentence.strip('\n')

        if 'class="match ' in sentence:
            sentence_in_paragraph_tag = get_deepest_match(bs_paragraph, sentence)
            term_in_sentence_tag = get_deepest_match(sentence_in_paragraph_tag, 'class="match ')
            is_italics = any(tag.name == 'em' for tag in term_in_sentence_tag.parents)

            sentence_bs = BeautifulSoup(sentence, "lxml")
            fragment = str(sentence_bs.body)
            fragment = re.sub(r'^<body>|</body>$', r'', fragment)
            fragment = re.sub(r'^<p>|</p>$', r'', fragment)

            if is_italics and not fragment.startswith('<em>'):
                fragment = "<em>{}</em>".format(fragment)

            if s_idx - 1 == last_match_idx:
                fragments[-1] += fragment
            else:
                fragments.append(fragment)
            last_match_idx = s_idx
    return fragments


def get_deepest_match(bs, html):
    result = None
    for tag in bs.find_all(True):
        if html in str(tag):
            result = tag
    assert result
    return result


def kt(q, numterms=10):
    s = ix.searcher()
    qp = QueryParser('content', ix.schema).parse(q)
    kt = s.search(qp).key_terms('content', numterms=numterms)
    return [term for (term, score) in kt]


os.chdir(sys.path[0])
indexdir = 'indexdir'

if not os.path.isdir(indexdir):
    os.mkdir(indexdir)

# rebuild = True
rebuild = False
if rebuild:
    ix = my_index.create_index(indexdir)
else:
    try:
        ix = index.open_dir(indexdir)
    except index.EmptyIndexError:
        ix = my_index.create_index(indexdir)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--interactive", help="load search index interactively", action="store_true")
    args = parser.parse_args()

    if args.interactive:
        s = ix.searcher()
    else:
        app.run()
