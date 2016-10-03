import os, sys, re
import urllib.parse

from flask import redirect
from flask import url_for
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
    return """<a href="javascript:void()" onclick="filterBook('{0}')">{0}</a>""".format(abbr)


@app.template_filter('example')
def example_link(s):
    return '<a href="/q/{}/">{}</a>'.format(pretty_url(s, is_href=True), s)


def pretty_url(s, is_href=False, undo=False):
    if undo:
        s = s.replace('\'', '"')
        s = urllib.parse.unquote_plus(s)
    else:
        s = s.replace('"', '\'')
        # valid path component chars are: '()*: http://stackoverflow.com/a/2375597/879
        # but browsers seem okay with []{} also
        safe = '[]{}\'()*:'
        if not is_href:
            safe += '"'
        s = urllib.parse.quote_plus(s, safe)
    return s


@app.route('/', methods=['GET', 'POST'])
@app.route('/q/', methods=['GET', 'POST'])
@app.route('/q/<input>/', methods=['GET', 'POST'])
def search_form(input=None):
    if request.method == 'POST':
        input = pretty_url(request.form['query'].strip())
        if input:
            return redirect(url_for('search_form', input=input))
    if not input:
        return render_template("search-form.html", books=Books.indexed, session_limit=session_limit, paragraph_limit=paragraph_limit)

    input = pretty_url(input, undo=True)
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

        result_len = len(results)
        if result_len <= session_limit:
            output = ['<h2 id="results">{} result{} for {}</h2>'.format(result_len, 's' if result_len > 1 else '', query)]
        else:
            output = ['<h2 id="results">Top {} of {} results for {}</h2>'.format(min(session_limit, result_len), result_len, query)]


        for h_idx, hit in enumerate(results):
            highlights = hit.highlights('content', top=50 if result_len == 1 else paragraph_limit)

            output.append('<a href="javascript:void(0)" class="display-toggle" onclick="toggleDisplay(this, \'hit-{}-long\')"> ► </a>'.format(h_idx))

            if result_len > 1 and 'content:' in str(query):
                direct_link = get_direct_link(hit, input)
                output.append('<a href="{1}" class="direct-link">{0[book_abbr]} {0[short]}</a>'.format(hit, direct_link))
            else:
                output.append('{0[book_abbr]} {0[short]}'.format(hit))

            output.append('<a href="{0[book_tree]}" class="book-link" target="_blank"><img src="/static/{1}.png"/></a>'.format(hit, hit['book_abbr'].lower()))
            output.append('<a href="{0[book_kindle]}" class="kindle-link" target="_blank"><img src="/static/kindle.png"/></a>'.format(hit))

            for key_term in hit['key_terms'][:5]:
                direct_link = get_direct_link(hit, key_term)
                output.append('<a class="key-term" href="{}">{}</a> '.format(direct_link, key_term))
            output.append('<br />')

            output.append('<span class="hit-long" id="hit-{1}-long" style="display: none">- {0[book_name]}<br />{0[long]}</span>'.format(hit, h_idx))

            if not highlights:
                output.append("<br />")
                continue

            output.append("<ul>")

            for p_idx, cm_paragraph in enumerate(filter(None, highlights.split('\n'))):
                paragraph = commonmark(cm_paragraph)
                # if False:
                if h_idx == 0 and p_idx < paragraph_limit:
                    excerpt = paragraph
                else:
                    if p_idx == paragraph_limit:
                        output.append("</ul><hr><ul>")
                    sentences = get_sentence_fragments(paragraph)
                    excerpt = ' <span class="omission">[...]</span> '.join(sentences)
                output.append("<li><p>{}</p></li>".format(excerpt))

            output.append("</ul>")
            # if result_len > 1 and h_idx == 0:
            #     output.append("<hr>")
            output.append("<br />")
        result = '\n'.join(output)

        scroll = 'session:' in str(query) and result_len == 1
        return render_template("search-form.html", scroll=scroll, books=Books.indexed, query=input, result=result, session_limit=session_limit, paragraph_limit=paragraph_limit)


def get_direct_link(hit, input):
    if hit['session']:
        session = hit['session'].replace(',', '')
        session = re.sub(r'^Session ', r'', session)
        result = "/q/{}/".format(pretty_url('session:"{}" {}'.format(session, input), is_href=True))
    else:
        # a bit hackish the way I use hit['short'] here... relies upon the fact that in this case that happens to be only the heading
        result = "/q/{}/".format(pretty_url('book:{} heading:"{}" {}'.format(hit['book_abbr'].lower(), hit['short'], input), is_href=True))
    return result


def get_sentence_fragments(paragraph):
    bs_paragraph = BeautifulSoup(paragraph, "lxml")

    fragments = []
    sentence_split = filter(None, re.split(r'(.*?(?:\.”|[\.\?!])[\s$])', paragraph))
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
    my_index.add_key_terms(ix)
else:
    try:
        ix = index.open_dir(indexdir)
    except index.EmptyIndexError:
        ix = my_index.create_index(indexdir)
        my_index.add_key_terms(ix)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--interactive", help="load search index interactively", action="store_true")
    args = parser.parse_args()

    if args.interactive:
        s = ix.searcher()
    else:
        app.run()
