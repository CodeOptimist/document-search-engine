import argparse
import os
import re
import sys
import urllib.parse

from CommonMark import commonmark
from bs4 import BeautifulSoup
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
sessions_per_page = 10
paragraph_limit = 3

@app.template_filter('book_link')
def book_link(abbr):
    return """<a href="javascript:void()" onclick="filterBook('{0}')">{0}</a>""".format(abbr)


@app.template_filter('example')
def example_link(q):
    return '<a href="/q/{}/">{}</a>'.format(urlize(q, in_href=True), q)


def pretty_redirect(s):
    s = urllib.parse.unquote(s)
    return redirect(s)


def urlize(s, in_href=False, undo=False):
    # print("Begin: {}".format(s))
    if undo:
        s = s.replace('\'', '"')
        s = urllib.parse.unquote_plus(s)
    else:
        # let's make book names lowercase (no undo)
        s = re.sub(r'\bbook:(\w+)', lambda m: m.group(0).lower(), s)
        # no undo for single quote to space since that is how Whoosh itself treats apostrophes
        s = s.replace("'", ' ').replace('"', '\'')
        # valid path component chars are: ()':* http://stackoverflow.com/a/2375597/879
        # but browsers seem okay with []{} also
        safe = '[]{}\'()*:'
        if not in_href:
            safe += '"'
        s = urllib.parse.quote_plus(s, safe)
    # print("End: {}".format(s))
    return s


@app.route('/', methods=['GET', 'POST'])
@app.route('/q/', methods=['GET', 'POST'])
@app.route('/q/<url_query>/', methods=['GET', 'POST'])
@app.route('/os/<os_query>/', methods=['GET', 'POST'])
@app.route('/q/<url_query>/s/', methods=['GET', 'POST'])
@app.route('/q/<url_query>/<url_num>/', methods=['GET', 'POST'])
def search_form(url_query=None, url_num=None, os_query=None):
    if os_query:
        return pretty_redirect(url_for('search_form', url_query=urlize(os_query)))

    if request.method == 'POST':
        url_query = urlize(request.form['query'].strip())
        if url_query:
            return pretty_redirect(url_for('search_form', url_query=url_query))
    if not url_query:
        return render_template("search-form.html", books=Books.indexed)

    query = urlize(url_query, undo=True)
    with ix.searcher() as searcher:
        is_single = request.base_url.endswith('/s/')
        if is_single:
            shorter_query = re.sub(r'\bsession:"(\d+)[^"]+"', r'session:\1', query)
            if shorter_query != query:
                qp = QueryParser('stemmed', my_index.search_schema).parse(shorter_query)
                results = searcher.search(qp, limit=2)
                if results.scored_length() == 1:
                    query = shorter_query

            url = url_for('search_form', url_query=urlize(query))
            url = re.sub(r'/s/$', r'/', url)
            return pretty_redirect(url)

        qp = QueryParser('stemmed', my_index.search_schema).parse(query)
        if isinstance(qp, _NullQuery):
            return render_template("search-form.html", books=Books.indexed)

        is_content_query = any(x in str(qp) for x in ('stemmed:', 'exact:', 'common:'))
        if is_content_query:
            try:
                num = int(url_num or 0)
                if num < 0:
                    raise ValueError
            except ValueError:
                return pretty_redirect(url_for('search_form', url_query=url_query))
            pagenum = int(num / sessions_per_page) + 1
            page = searcher.search_page(qp, pagenum, pagelen=sessions_per_page)
        else:
            page = searcher.search_page(qp, 1, pagelen=150)

        page.results.fragmenter = ParagraphFragmenter()
        page.results.order = highlight.SCORE
        page.results.scorer = ConsistentFragmentScorer()
        page.results.formatter = highlight.HtmlFormatter(between='')

        if page.total <= page.pagelen:
            output = ['<h2 id="results">{} result{} for {}</h2>'.format(page.total, 's' if page.total > 1 else '', qp)]
        else:
            output = ['<h2 id="results">Results {} to {} of {} for {}</h2>'.format(page.offset + 1, page.offset + page.pagelen, page.total, qp)]

        for h_idx, hit in enumerate(page):
            if 'exact:' in str(qp):
                highlight_field = 'exact'
            elif 'common:' in str(qp):
                highlight_field = 'common'
            else:
                highlight_field = 'stemmed'
            highlights = hit.highlights(highlight_field, top=50 if page.total == 1 else paragraph_limit)

            output.append('<a href="javascript:void(0)" class="display-toggle" onclick="toggleDisplay(this, \'hit-{}-long\')"> ► </a>'.format(h_idx))

            direct_link = get_single_result_link(hit, query)
            if page.total > 1 and is_content_query:
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
                if page.pagenum == 1 and h_idx == 0 and p_idx < paragraph_limit:
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

        previous = '<a href="/q/{}/{}">← Previous</a>'.format(url_query, str(page.offset - sessions_per_page) + '/' if page.offset > sessions_per_page else '') if page.offset >= page.pagelen else ''
        next = '<a href="/q/{}/{}">Next →</a>'.format(url_query, str(page.offset + page.pagelen) + '/') if page.total - page.offset - page.pagelen > 0 else ''
        pagination = '{} &nbsp; {}'.format(previous, next)

        scroll = ('session:' in str(qp) and page.total == 1) or url_num is not None
        return render_template("search-form.html", books=Books.indexed, query=query, result=result, pagination=pagination, scroll=scroll)


def get_single_result_link(hit, query):
    if hit['session']:
        session = re.sub(r'[^\w’]', ' ', hit['session'])
        session = re.sub(r'\s+', ' ', session).strip()
        session = re.sub(r'^session ', r'', session, flags=re.IGNORECASE)
        result = "/q/{}/s/".format(urlize('session:"{}" {}'.format(session, query), in_href=True))
    else:
        # a bit hackish, in this case 'short' happens to be only the heading
        heading = re.sub(r'[^\w’]', ' ', hit['short'])
        heading = re.sub(r'\s+', ' ', heading).strip()
        result = "/q/{}/s/".format(urlize('book:{} heading:"{}" {}'.format(hit['book_abbr'].lower(), heading, query), in_href=True))
    return result


def get_sentence_fragments(paragraph):
    paragraph_soup = BeautifulSoup(paragraph, 'lxml')

    fragments = []
    sentence_split = filter(None, re.split(r'(.*?(?:\.”|(?<!\b\w)(?<!\b(?:Dr|Sr|Jr|Mr|Ms))(?<!\bMrs)\.|[?!])[\s$])', paragraph))
    last_match_idx = None
    for s_idx, raw_sentence in enumerate(sentence_split):
        raw_sentence = raw_sentence.strip('\n')
        sentence_soup = BeautifulSoup(raw_sentence, 'lxml')

        if 'class="match ' in raw_sentence:
            sentence_in_paragraph_tag = get_deepest_match(paragraph_soup, sentence_soup)
            is_italics = sentence_in_paragraph_tag.name == 'em' or any(tag.name == 'em' for tag in sentence_in_paragraph_tag.parents)

            sentence = str(sentence_soup.body)
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


def get_deepest_match(paragraph_soup, sentence_soup):
    end_punctuation_re = r'(^\W+|\W+$)'
    html_strings = re.sub(end_punctuation_re, '', ''.join(sentence_soup.strings))

    result = None
    for tag in paragraph_soup.find_all(True):
        tag_strings = re.sub(end_punctuation_re, '', ''.join(tag.strings))
        if html_strings in tag_strings:
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