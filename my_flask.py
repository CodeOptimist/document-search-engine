import os, sys, re
from whoosh import highlight, index
from whoosh.highlight import BasicFragmentScorer
from whoosh.qparser import QueryParser
from CommonMark import commonmark
from whoosh.query.qcore import _NullQuery
import argparse

from my_whoosh import ParagraphFragmenter, TokenPosFormatter, ConsistentFragmentScorer
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
session_limit = 5
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

        output = ["## Results for {}".format(query)]
        for hit in results:
            results.fragmenter = ParagraphFragmenter()
            results.order = highlight.SCORE
            results.scorer = ConsistentFragmentScorer()
            results.formatter = TokenPosFormatter()
            paragraph_idxs = get_matching_paragraph_idxs(results, hit)
            # print(paragraph_idxs)

            results.fragmenter = highlight.WholeFragmenter(charlimit=None)
            results.scorer = BasicFragmentScorer()
            results.formatter = highlight.HtmlFormatter()
            highlights = hit.highlights('content', top=paragraph_limit)
            if not highlights:
                highlights = hit['content']
            highlights = highlights.split('\n')

            output.append("""
<details>
<span style="font-size: 0.9em">- {0[book_name]}<br />{0[long]}</span>
<summary>{0[book_abbr]} {0[short]}<a href="{0[book_url]}" target="_blank"><img src="/static/{0[book_abbr]}.png" style="vertical-align: text-bottom; height: 1.5em; padding: 0em 0.5em;"/></a></summary>
</details>
""".format(hit))
            for idx in paragraph_idxs:
                matching_paragraph = highlights[idx]
                output.append("* {}".format(matching_paragraph))
            output.append('<br />')

        output_str = '\n\n'.join(output)
        result = commonmark(output_str)
        return render_template("search-form.html", books=Books.indexed, query=input, result=result, session_limit=session_limit, paragraph_limit=paragraph_limit)


def get_matching_paragraph_idxs(results, hit):
    matching_paragraph_idxs = []
    fragments_tokens_pos = hit.highlights('content', top=paragraph_limit).split('\n')
    fragments_tokens_pos = filter(None, fragments_tokens_pos)

    for fragment_tokens_pos in fragments_tokens_pos:
        fragment_pos = int(fragment_tokens_pos.split()[0])
        paragraph_idx = hit['content'][:fragment_pos].count('\n')
        if paragraph_idx not in matching_paragraph_idxs:
            matching_paragraph_idxs.append(paragraph_idx)
    return matching_paragraph_idxs


def kt(q, numterms=10):
    s = ix.searcher()
    qp = QueryParser('content', ix.schema).parse(q)
    kt = s.search(qp).key_terms('content', numterms=numterms)
    return [term for (term, score) in kt]


os.chdir(sys.path[0])
indexdir = 'indexdir'

if not os.path.isdir(indexdir):
    os.mkdir(indexdir)

rebuild = True
# rebuild = False
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
