import argparse
import os
import re
import sys
import urllib.parse

from CommonMark import commonmark
from bs4 import BeautifulSoup
from bs4.dammit import EntitySubstitution
from flask import Flask, request, render_template
from flask import redirect
from flask import url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from whoosh import highlight, index
from whoosh.qparser import QueryParser
# noinspection PyProtectedMember
from whoosh.query.qcore import _NullQuery

import my_index
from books import Books
from my_whoosh import ParagraphFragmenter, ConsistentFragmentScorer

app = Flask(__name__)
limiter = Limiter(
    app,
    key_func=get_remote_address,
    global_limits=["30 per minute", "200 per hour", "1000 per day"]
)
session_limit = 7
paragraph_limit = 3

HTML_ENTITY_TO_CHARACTER = {"&{};".format(k): v for k, v in EntitySubstitution.HTML_ENTITY_TO_CHARACTER.items()}
HTML_ENTITY_TO_CHARACTER_RE = re.compile(r'|'.join(HTML_ENTITY_TO_CHARACTER.keys()))

@app.template_filter('book_link')
def book_link(abbr):
    return """<a href="javascript:void()" onclick="filterBook('{0}')">{0}</a>""".format(abbr)


@app.template_filter('example')
def example_link(q):
    return '<a href="/q/{}/">{}</a>'.format(urlize(q, in_href=True), q)


def urlize(s, in_href=False, undo=False):
    #print("Begin: {}".format(s))
    if undo:
        s = s.replace('\'', '"')
        s = urllib.parse.unquote_plus(s)
    else:
        s = s.replace('"', '\'')
        # valid path component chars are: ()':* http://stackoverflow.com/a/2375597/879
        # but browsers seem okay with []{} also
        safe = '[]{}\'()*:'
        if not in_href:
            safe += '"'
        s = urllib.parse.quote_plus(s, safe)
    #print("End: {}".format(s))
    return s


@app.route('/', methods=['GET', 'POST'])
@app.route('/q/', methods=['GET', 'POST'])
@app.route('/q/<query>/', methods=['GET', 'POST'])
def search_form(query=None):
    if request.method == 'POST':
        query = urlize(request.form['query'].strip())
        if query:
            url = url_for('search_form', query=query).replace('%27', "'")
            return redirect(url)
    if not query:
        return render_template("search-form.html", books=Books.indexed)

    query = urlize(query, undo=True)
    with ix.searcher() as searcher:
        query = re.sub(r'\bbook:(\w+)', lambda m: m.group(0).lower(), query)
        qp = QueryParser('stemmed', my_index.search_schema).parse(query)
        if isinstance(qp, _NullQuery):
            return render_template("search-form.html", books=Books.indexed)

        is_content_query = 'stemmed:' in str(qp) or 'exact:' in str(qp)
        limit = session_limit if is_content_query else 150
        results = searcher.search(qp, limit=limit)

        results.fragmenter = ParagraphFragmenter()
        results.order = highlight.SCORE
        results.scorer = ConsistentFragmentScorer()
        results.formatter = highlight.HtmlFormatter(between='')

        result_len = len(results)
        if result_len <= limit:
            output = ['<h2 id="results">{} result{} for {}</h2>'.format(result_len, 's' if result_len > 1 else '', qp)]
        else:
            output = ['<h2 id="results">Top {} of {} results for {}</h2>'.format(min(limit, result_len), result_len, qp)]

        for h_idx, hit in enumerate(results):
            highlights = hit.highlights('exact' if 'exact:' in str(qp) else 'stemmed', top=50 if result_len == 1 else paragraph_limit)

            output.append('<a href="javascript:void(0)" class="display-toggle" onclick="toggleDisplay(this, \'hit-{}-long\')"> ► </a>'.format(h_idx))

            direct_link = get_single_result_link(hit, query)
            if result_len > 1 and is_content_query:
                output.append('<a href="{1}" class="direct-link">{0[book_abbr]} {0[short]}</a>'.format(hit, direct_link))
            else:
                output.append('{0[book_abbr]} {0[short]}'.format(hit))

            output.append('<a href="{0[book_tree]}" class="book-link" target="_blank"><img src="/static/{1}.png"/></a>'.format(hit, hit['book_abbr'].lower()))
            output.append('<a href="{0[book_kindle]}" class="kindle-link" target="_blank"><img src="/static/kindle.png"/></a>'.format(hit))

            for key_term in hit['key_terms'][:5]:
                term_link = get_single_result_link(hit, key_term)
                output.append('<a class="key-term" href="{}">{}</a> '.format(term_link, key_term))
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
                    excerpt = '<a href="{}" class="omission"> [...] </a>'.format(direct_link).join(sentences)
                output.append("<li><p>{}</p></li>".format(excerpt))
            output.append("</ul>")

            # if result_len > 1 and h_idx == 0:
            #     output.append("<hr>")
            output.append("<br />")
        result = '\n'.join(output)

        scroll = 'session:' in str(qp) and result_len == 1
        return render_template("search-form.html", books=Books.indexed, query=query, result=result, scroll=scroll)


def get_single_result_link(hit, query):
    if hit['session']:
        session = re.sub(r'[^\w’]', ' ', hit['session'])
        session = re.sub(r'\s+', ' ', session)
        session = re.sub(r'^session ', r'', session, flags=re.IGNORECASE)
        result = "/q/{}/".format(urlize('session:"{}" {}'.format(session, query), in_href=True))
    else:
        # a bit hackish, in this case 'short' happens to be only the heading
        heading = re.sub(r'[^\w’]', ' ', hit['short'])
        heading = re.sub(r'\s+', ' ', heading)
        result = "/q/{}/".format(urlize('book:{} heading:"{}" {}'.format(hit['book_abbr'].lower(), heading, query), in_href=True))
    return result


def get_sentence_fragments(paragraph):
    paragraph_bs = BeautifulSoup(paragraph, 'lxml')

    fragments = []
    sentence_split = filter(None, re.split(r'(.*?(?:\.”|(?<!\b\w)(?<!\b(?:Dr|Sr|Jr|Mr|Ms))(?<!\bMrs)\.|[?!])[\s$])', paragraph))
    last_match_idx = None
    for s_idx, raw_sentence in enumerate(sentence_split):
        raw_sentence = raw_sentence.strip('\n')

        if 'class="match ' in raw_sentence:
            sentence_wout_entities = HTML_ENTITY_TO_CHARACTER_RE.sub(lambda x: HTML_ENTITY_TO_CHARACTER[x.group()], raw_sentence)
            sentence_in_paragraph_tag = get_deepest_match(paragraph_bs, sentence_wout_entities)
            term_in_sentence_tag = get_deepest_match(sentence_in_paragraph_tag, 'class="match ')
            is_italics = any(tag.name == 'em' for tag in term_in_sentence_tag.parents)

            sentence_bs = BeautifulSoup(sentence_wout_entities, "lxml")
            sentence = str(sentence_bs.body)
            sentence = re.sub(r'^<body>|</body>$', r'', sentence)
            sentence = re.sub(r'^<p>|</p>$', r'', sentence)

            if is_italics and not sentence.startswith('<em>'):
                sentence = "<em>{}</em>".format(sentence)

            is_adjacent = s_idx - 1 == last_match_idx
            if is_adjacent:
                fragments[-1] += sentence
            else:
                fragments.append(sentence)
            last_match_idx = s_idx
    return fragments


def get_deepest_match(bs, html):
    result = None
    for tag in bs.find_all(True):
        if html in str(tag):
            result = tag
    assert result
    return result


def main():
    global ix
    os.chdir(sys.path[0])

    index_dir = 'index'
    if __name__ == '__main__':
        parser = argparse.ArgumentParser()
        parser.add_argument("-i", "--interactive", help="load search index interactively", action='store_true')
        parser.add_argument("-r", "--rebuild", help="rebuild index", nargs='?', const="index")
        args = parser.parse_args()

        if args.rebuild:
            ix = my_index.create_index_and_key_terms(args.rebuild)
        else:
            ix = get_idx(index_dir)
            if not args.interactive:
                app.run()
    else:
        ix = get_idx(index_dir)


def get_idx(index_dir):
    try:
        ix = index.open_dir(index_dir)
    except index.EmptyIndexError:
        ix = my_index.create_index_and_key_terms(index_dir)
    return ix


main()