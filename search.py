import os, sys, re
from whoosh import highlight, index
from whoosh.qparser import QueryParser
from CommonMark import commonmark
from my_whoosh import ParagraphFragmenter, TokenPosFormatter
from books import Books
import spec

from flask import Flask, request, render_template
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

app = Flask(__name__)
limiter = Limiter(
    app,
    key_func=get_remote_address,
    global_limits=["10 per minute", "100 per hour", "1000 per day"]
)
session_limit = 10
paragraph_limit = 3
ix = None


def init():
    global ix
    if not ix:
        os.chdir(sys.path[0])
        indexdir = 'indexdir'

        try:
            ix = index.open_dir(indexdir)
        except index.EmptyIndexError:
            if not os.path.isdir(indexdir):
                os.mkdir(indexdir)
            ix = spec.create_index(indexdir)
        # ix = spec.create_index(indexdir)


@app.route('/')
def search_form():
    init()
    return render_template("search-form.html", books=Books.indexed, session_limit=session_limit, paragraph_limit=paragraph_limit)


@app.route('/', methods=['POST'])
def search_form_post():
    init()
    text = request.form['text']

    with ix.searcher() as searcher:
        text = re.sub(r'\bbook:(\w+)', lambda m: m.group(0).lower(), text)
        query = QueryParser('session', ix.schema).parse(text)
        results = searcher.search(query, limit=session_limit)

        output = []
        for hit in results:
            paragraph_idxs = get_matching_paragraph_idxs(results, hit)
            # print(paragraph_idxs)

            results.fragmenter = highlight.WholeFragmenter(charlimit=None)
            results.formatter = highlight.HtmlFormatter()
            highlights = hit.highlights('session', top=paragraph_limit)
            if not highlights:
                highlights = hit['session']
            highlights = highlights.split('\n')

            output.append("""
<details>
<span style="font-size: 0.9em">- {0[book_name]}<br />{1}- {0[chapter_title]}</span>
<summary>{0[book_abbr]} {0[chapter_id]} {0[session_id]}</summary>
</details>
""".format(hit, "- {0[part_title]}<br />".format(hit) if 'part_title' in hit else ''))
            for idx in paragraph_idxs:
                matching_paragraph = highlights[idx]
                output.append("* {}".format(matching_paragraph))
            output.append('<br />')

        output_str = '\n\n'.join(output)
        result = commonmark(output_str)
        return render_template("search-form.html", books=Books.indexed, query=text, result=result, session_limit=session_limit, paragraph_limit=paragraph_limit)


def get_matching_paragraph_idxs(results, hit):
    matching_paragraph_idxs = []
    results.fragmenter = ParagraphFragmenter()
    results.order = highlight.SCORE
    results.formatter = TokenPosFormatter()

    fragments_tokens_pos = hit.highlights('session', top=paragraph_limit).split('\n')
    fragments_tokens_pos = filter(None, fragments_tokens_pos)

    for fragment_tokens_pos in fragments_tokens_pos:
        fragment_pos = int(fragment_tokens_pos.split()[0])
        paragraph_idx = hit['session'][:fragment_pos].count('\n')
        if paragraph_idx not in matching_paragraph_idxs:
            matching_paragraph_idxs.append(paragraph_idx)
    return matching_paragraph_idxs

if __name__ == '__main__':
    app.run()
