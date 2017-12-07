# coding=utf-8
import argparse
import os
import re
import sys
import urllib.parse
import html

from CommonMark import commonmark
from bs4 import BeautifulSoup
from flask import Flask, request, render_template
from flask import redirect
from flask import url_for
from whoosh import highlight, index
from whoosh.qparser import QueryParser
from whoosh.qparser.dateparse import DateParserPlugin
# noinspection PyProtectedMember
from whoosh.query.qcore import NullQuery
from whoosh.scoring import BM25F

import my_index
from books import Books
from my_whoosh import ParagraphFragmenter, ConsistentFragmentScorer, DescDateBM25F, AscDateBM25F

app = Flask(__name__)
sessions_per_content_page = 10
sessions_per_listing_page = 150
multiple_hit_excerpt_limit = 3
single_hit_excerpt_limit = 50    # effectively ALL of them, I would think
# excerpt_omission_threshold = 3
excerpt_omission_threshold = max(single_hit_excerpt_limit, multiple_hit_excerpt_limit)
default_field = 'stemmed'


@app.template_filter('volumes_link')
def book_link_html(tpl):
    abbr, name = tpl
    return """<a href="javascript:void()" title="{1}" onclick="filterBook('{0}')">{0}</a>""".format(abbr, html.escape(name))


@app.template_filter('book_link')
def book_link(book):
    return book_link_html((book['abbr'], book['name']))


@app.context_processor
def template_functions():
    def example(q, desc):
        return '<a href="/q/{}/" title="{}">{}</a>'.format(urlize(q, in_href=True), html.escape(desc), q)
    return dict(example=example)


def pretty_redirect(url):
    url = urllib.parse.unquote(url)
    return redirect(url)


def stateful_url_for(endpoint, **kwargs):
    new_state = url_state.copy()
    new_state.update(kwargs)
    return url_for(endpoint, **new_state)


def stateful_redirect(endpoint, **kwargs):
    return pretty_redirect(stateful_url_for(endpoint, **kwargs))


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


url_state = {}
# order is important, url_for() returns the last matching
@app.route('/q/', methods=['GET', 'POST'])
@app.route('/', methods=['GET', 'POST'])
@app.route('/h/<hit_order>/', methods=['GET', 'POST'])
@app.route('/e/<excerpt_order>/', methods=['GET', 'POST'])
@app.route('/h/<hit_order>/e/<excerpt_order>/', methods=['GET', 'POST'])
@app.route('/os/<os_query>/', methods=['GET', 'POST'])
@app.route('/q/<q_query>/s/', methods=['GET', 'POST'])
@app.route('/q/<q_query>/', methods=['GET', 'POST'])
@app.route('/q/<q_query>/<page_num>/', methods=['GET', 'POST'])
@app.route('/q/<q_query>/h/<hit_order>/', methods=['GET', 'POST'])
@app.route('/q/<q_query>/h/<hit_order>/<page_num>/', methods=['GET', 'POST'])
@app.route('/q/<q_query>/e/<excerpt_order>/', methods=['GET', 'POST'])
@app.route('/q/<q_query>/e/<excerpt_order>/<page_num>/', methods=['GET', 'POST'])
@app.route('/q/<q_query>/h/<hit_order>/e/<excerpt_order>/', methods=['GET', 'POST'])
@app.route('/q/<q_query>/h/<hit_order>/e/<excerpt_order>/<page_num>/', methods=['GET', 'POST'])
def search_form(os_query=None, q_query=None, hit_order=None, excerpt_order=None, page_num=None):
    url_state.update(locals().copy())

    # redirect POST to GET
    if request.method == 'POST':
        q_query = urlize(request.form['query'].strip())
        _, hit_order, excerpt_order = get_valid_order(request.form['hit-order'], request.form['excerpt-order'])
        hit_order = None if hit_order == 'rel' else hit_order
        excerpt_order = None if excerpt_order == 'rel' else excerpt_order
        return pretty_redirect(url_for('search_form', q_query=q_query, hit_order=hit_order, excerpt_order=excerpt_order))

    if request.method == 'GET':
        order_was_bad, url_state['hit_order'], url_state['excerpt_order'] = get_valid_order(hit_order, excerpt_order)
        num_was_bad, url_state['page_num'] = get_valid_num(page_num)
        if os_query:
            url_state['os_query'] = None
            url_state['q_query'] = urlize(os_query)
            url_state['page_num'] = None
        if os_query or order_was_bad or num_was_bad:
            return stateful_redirect('search_form')
        
        if not url_state['q_query']:
            return render_template("search-form.html", **url_state, books=Books.indexed, doc_count=ix.doc_count())

        # in a GET the ? is stripped, even if it's before a /, so must always use %3F for the GET url (urlize(undo=False))
        # but oddly enough flask here shows it as ? even though it keeps e.g. + for spaces, so we put it back to %3F
        url_state['q_query'] = url_state['q_query'].replace('?', '%3F')
        plain_query = urlize(url_state['q_query'], undo=True)
        return search_whoosh(plain_query)


def get_valid_order(hit_order, excerpt_order):
    valid_ho = hit_order if hit_order in (None, 'rel', 'asc', 'desc') else None
    valid_eo = excerpt_order if excerpt_order in (None, 'rel', 'pos') else None
    was_bad = valid_ho != hit_order or valid_eo != excerpt_order
    return was_bad, valid_ho, valid_eo


def get_valid_num(num):
    try:
        num = int(num or 0)
        if num < 0:
            raise ValueError
        was_bad = False
        result = int(num / sessions_per_content_page) + 1
    except ValueError:
        was_bad = True
        result = None
    return was_bad, result


def search_whoosh(plain_query):
    weighting = AscDateBM25F if url_state['hit_order'] == 'asc' else DescDateBM25F if url_state['hit_order'] == 'desc' else BM25F
    with ix.searcher(weighting=weighting) as searcher:
        to_shorten = request.base_url.endswith('/s/')
        if to_shorten:
            return pretty_redirect(get_short_url(searcher, plain_query))

        qp = QueryParser(default_field, my_index.search_schema)
        qp.add_plugin(DateParserPlugin())
        # todo this is pretty ugly
        try:
            qp = qp.parse(plain_query)
        except:
            dateless_query = re.sub(r'\bdate:\[.*\]', r'', plain_query, re.IGNORECASE)
            return stateful_redirect('search_form', q_query=urlize(dateless_query) or None)

        if isinstance(qp, type(NullQuery)):
            return stateful_redirect('search_form', q_query=None)

        highlight_field = None
        for field in ('exact', 'common', 'stemmed'):
            if field + ':' in str(qp):
                highlight_field = field
                break

        is_content_search = highlight_field is not None
        if is_content_search:
            page_results = searcher.search_page(qp, pagenum=url_state['page_num'], pagelen=sessions_per_content_page)
        else:
            page_results = searcher.search_page(qp, pagenum=1, pagelen=sessions_per_listing_page)

        page_results.results.fragmenter = ParagraphFragmenter()
        page_results.results.order = highlight.FIRST if url_state['excerpt_order'] == 'pos' else highlight.SCORE
        page_results.results.scorer = ConsistentFragmentScorer()
        page_results.results.formatter = highlight.HtmlFormatter(between='')

        result = {}
        result['description'], result['results'] = get_html_results(plain_query, qp, page_results, highlight_field)
        result['pagination'] = get_html_pagination(page_results)
        result['scroll'] = "results"
        return render_template("search-form.html", **url_state, **result, plain_query=plain_query, books=Books.indexed, doc_count=ix.doc_count())


def get_html_pagination(page_results):
    prev_page_num = str(page_results.offset - sessions_per_content_page) if page_results.offset > sessions_per_content_page else None
    prev_url = stateful_url_for('search_form', page_num=prev_page_num)
    prev = '<a href="{}">← Previous</a>'.format(prev_url) if page_results.offset >= page_results.pagelen else ''

    next_page_num = str(page_results.offset + page_results.pagelen)
    next_url = stateful_url_for('search_form', page_num=next_page_num)
    next = '<a href="{}">Next →</a>'.format(next_url) if page_results.total - page_results.offset - page_results.pagelen > 0 else ''

    result = '{} &nbsp; {}'.format(prev, next)
    return result


def get_short_url(searcher, plain_query):
    shorter_query = re.sub(r'\bsession:"(\d+)[^"]+"', r'session:\1', plain_query)
    if shorter_query != plain_query:
        qp = QueryParser(default_field, my_index.search_schema).parse(shorter_query)
        results = searcher.search(qp, limit=2)
        if results.scored_length() == 1:
            plain_query = shorter_query
    result = stateful_url_for('search_form', q_query=urlize(plain_query), page_num=None)
    result = re.sub(r'/s/$', r'/', result)
    return result


def get_html_results(plain_query, qp, page_results, highlight_field):
    result = []

    is_single_page = page_results.total <= page_results.pagelen
    if is_single_page:
        heading = '<h2 id="results">{} result{} for {}</h2>'.format(page_results.total, 's' if page_results.total > 1 else '', qp)
    else:
        heading = '<h2 id="results">Results {} to {} of {} for {}</h2>'.format(page_results.offset + 1, page_results.offset + page_results.pagelen, page_results.total, qp)
    result.append(heading)

    description = None
    for hit_idx, hit in enumerate(page_results):
        result.append('<div class="hit">')
        result.append('<a href="javascript:void(0)" class="display-toggle" onclick="toggleDisplay(this, \'hit-{}-long\')">►</a>'.format(hit_idx))

        hit_link = get_single_result_link(hit, plain_query)
        is_listing_page = highlight_field is None
        nowhere_to_go = page_results.total == 1 or is_listing_page
        if nowhere_to_go:
            result.append('<span class="heading">{0[book_abbr]} {0[short]}</span>'.format(hit))
        else:
            result.append('<a href="{1}" class="heading">{0[book_abbr]} {0[short]}</a>'.format(hit, hit_link))

        icon = re.sub(r'(tes|tps|tecs)\d', r'\1', hit['book_abbr'].lower())
        result.append('<a href="{0[book_tree]}" class="book-link" target="_blank"><img src="/static/{1}.png"/></a>'.format(hit, icon))
        result.append('<a href="{0[book_kindle]}" class="kindle-link" target="_blank"><img src="/static/kindle.png"/></a>'.format(hit))

        for key_term in hit['key_terms'][:5]:
            term_link = get_single_result_link(hit, key_term)
            result.append('<a class="key-term" href="{}">{}</a> '.format(term_link, key_term))
        result.append('<br />')

        result.append('<span class="hit-long" id="hit-{1}-long" style="display: none">- {0[book_name]}<br />{0[long]}</span>'.format(hit, hit_idx))

        hit_description, highlights = get_html_highlights(highlight_field, page_results, hit_idx, hit, hit_link)
        if hit_idx == 0:
            description = "{} results.  {}".format(page_results.total, hit_description)
        result.extend(highlights)
        result.append("</div>")

    result = '\n'.join(result)
    return description, result


def get_html_highlights(highlight_field, page_results, hit_idx, hit, hit_link):
    result = []
    description = None
    highlights = hit.highlights(highlight_field or default_field, top=single_hit_excerpt_limit if page_results.total == 1 else multiple_hit_excerpt_limit + 1)

    verbatim_copy = False
    if page_results.total == 1 and url_state['excerpt_order'] == 'pos':
        full_doc_text = hit['exact']
        num_highlight_paragraphs = highlights.count('\n')
        num_doc_paragraphs = full_doc_text.count('\n\n')
        coverage = num_highlight_paragraphs / num_doc_paragraphs
        if len(full_doc_text) > 1500 and (coverage > 0.5 or num_highlight_paragraphs == single_hit_excerpt_limit):
            result.append("<h4>Your search has returned many paragraphs of this document in their original order.")
            result.append("As this is quite similar to the copyrighted work, only <em>sentences</em> matching the search terms are displayed.")
            result.append('To view full paragraphs please narrow your search or <a onclick="'
                          "document.getElementById('excerpt-order-rel').click();document.getElementById('submit').click();"
                          '" href="javascript:void(0);">sort excerpts by relevance.</a></h4>')
            verbatim_copy = True

    excerpts = []
    for p_idx, cm_paragraph in enumerate(filter(None, highlights.split('\n'))):
        if page_results.total > 1 and p_idx == multiple_hit_excerpt_limit:
            excerpts.append('<li><p><a href="{}"> More... </a></p></li>'.format(hit_link))
            continue

        paragraph = commonmark(cm_paragraph).strip()

        gets_full_paragraph = page_results.pagenum == 1 and hit_idx == 0 and p_idx < excerpt_omission_threshold
        if gets_full_paragraph and not verbatim_copy:
            excerpts.append("<li>{}</li>".format(paragraph))
            if p_idx == 0:
                description = BeautifulSoup(paragraph, 'lxml').text.strip()
        else:
            if p_idx == excerpt_omission_threshold:
                excerpts.append("</ul><hr>")
                excerpts.append('<ul class="excerpts">')
            sentences = get_sentence_fragments(paragraph)
            if page_results.total > 1:
                excerpt = '<a href="{}" class="omission"> [...] </a>'.format(hit_link).join(sentences)
            else:
                excerpt = ' [...] '.join(sentences)
            excerpts.append("<li><p>{}</p></li>".format(excerpt))

    if excerpts:
        result.append('<ul class="excerpts">')
        result.extend(excerpts)
        result.append("</ul>")
    return description, result


def get_single_result_link(hit, plain_query):
    if hit['session']:
        session = re.sub(r'[^\w’]', ' ', hit['session'])
        session = re.sub(r'\s+', ' ', session).strip()
        session = re.sub(r'^session ', r'', session, flags=re.IGNORECASE)
        q_query = urlize('session:"{}" {}'.format(session, plain_query), in_href=True)
        result = stateful_url_for('search_form', q_query=q_query, page_num=None) + 's/'
    else:
        # a bit hackish, in this case 'short' happens to be only the heading
        heading = re.sub(r'[^\w’]', ' ', hit['short'])
        heading = re.sub(r'\s+', ' ', heading).strip()
        q_query = urlize('book:{} heading:"{}" {}'.format(hit['book_abbr'].lower(), heading, plain_query), in_href=True)
        result = stateful_url_for('search_form', q_query=q_query, page_num=None) + 's/'
    return result


def get_sentence_fragments(paragraph):
    paragraph_soup = BeautifulSoup(paragraph, 'lxml')

    result = []
    sentence_split = filter(None, re.split(r'(.*?(?:\.”|(?<!\b\w)(?<!\b(?:Dr|Sr|Jr|Mr|Ms))(?<!\bMrs)\.|[?!])[\s$])', paragraph))
    last_match_idx = None
    raw_sentence = ''
    for s_idx, raw_sentence in enumerate(sentence_split):
        raw_sentence = raw_sentence.strip('\n')

        if 'class="match ' not in raw_sentence:
            if s_idx == 0:
                result.append('')
            continue

        sentence_soup = BeautifulSoup(raw_sentence, 'lxml')
        deepest_sentence_tag = get_deepest_tag(sentence_soup, paragraph_soup)
        is_italics = deepest_sentence_tag.name == 'em' or any(tag.name == 'em' for tag in deepest_sentence_tag.parents)

        sentence = str(sentence_soup.body)
        sentence = re.sub(r'^<body>|</body>$', r'', sentence)
        sentence = re.sub(r'^<p>|</p>$', r'', sentence)
        sentence = re.sub(r'^<li>|</li>$', r'', sentence)

        if is_italics and not sentence.startswith('<em>'):
            sentence = "<em>{}</em>".format(sentence)

        is_adjacent = s_idx - 1 == last_match_idx
        if is_adjacent:
            result[-1] += sentence
        else:
            result.append(sentence)
        last_match_idx = s_idx
    if 'class="match ' not in raw_sentence:
        result.append('')
    return result


def get_deepest_tag(needle_soup, haystack_soup):
    punctuation_ends_re = r'(^\W+|\W+$)'
    needle_strings = re.sub(punctuation_ends_re, '', ''.join(needle_soup.strings))

    result = None
    for tag in haystack_soup.find_all(True):
        tag_strings = re.sub(punctuation_ends_re, '', ''.join(tag.strings))
        if needle_strings in tag_strings:
            result = tag

    assert result
    return result


def get_idx(index_dir):
    try:
        ix = index.open_dir(index_dir)
    except index.EmptyIndexError:
        ix = my_index.create_index_and_key_terms(index_dir)
    return ix


def test():
    from whoosh.query import Every
    results = ix.searcher().search(Every('session'), limit=None)
    for result in results:
        pass


def main():
    global ix
    os.chdir(sys.path[0])

    index_dir = 'index'
    if __name__ == '__main__':
        parser = argparse.ArgumentParser()
        parser.add_argument("-i", "--interactive", help="load search index interactively", action='store_true')
        parser.add_argument("-r", "--rebuild", help="rebuild index", nargs='?', const="index")
        parser.add_argument("-t", "--test", help="test", action='store_true')
        args = parser.parse_args()

        if args.rebuild:
            ix = my_index.create_index_and_key_terms(args.rebuild)
        else:
            ix = get_idx(index_dir)
            if args.test:
                test()
            elif not args.interactive:
                app.run()
    else:
        ix = get_idx(index_dir)


main()