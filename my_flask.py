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
multiple_result_paragraph_limit = 3
single_result_paragraph_limit = 50    # effectively ALL of them, I would think
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


# order is important, url_for() returns the last
@app.route('/q/', methods=['GET', 'POST'])
@app.route('/', methods=['GET', 'POST'])
@app.route('/h/<hit_order>/', methods=['GET', 'POST'])
@app.route('/e/<excerpt_order>/', methods=['GET', 'POST'])
@app.route('/h/<hit_order>/e/<excerpt_order>/', methods=['GET', 'POST'])
@app.route('/os/<os_query>/', methods=['GET', 'POST'])
@app.route('/q/<url_query>/s/', methods=['GET', 'POST'])
@app.route('/q/<url_query>/', methods=['GET', 'POST'])
@app.route('/q/<url_query>/<url_num>/', methods=['GET', 'POST'])
@app.route('/q/<url_query>/h/<hit_order>/', methods=['GET', 'POST'])
@app.route('/q/<url_query>/h/<hit_order>/<url_num>/', methods=['GET', 'POST'])
@app.route('/q/<url_query>/e/<excerpt_order>/', methods=['GET', 'POST'])
@app.route('/q/<url_query>/e/<excerpt_order>/<url_num>/', methods=['GET', 'POST'])
@app.route('/q/<url_query>/h/<hit_order>/e/<excerpt_order>/', methods=['GET', 'POST'])
@app.route('/q/<url_query>/h/<hit_order>/e/<excerpt_order>/<url_num>/', methods=['GET', 'POST'])
def search_form(os_query=None, url_query=None, hit_order=None, excerpt_order=None, url_num=None):
    if os_query:
        return pretty_redirect(url_for('search_form', url_query=urlize(os_query)))

    # redirect POST to GET
    if request.method == 'POST':
        url_query = urlize(request.form['query'].strip())
        if url_query:
            _, valid_hit_order, valid_excerpt_order = get_valid_order(request.form['hit-order'], request.form['excerpt-order'])
            valid_hit_order = None if valid_hit_order == 'rel' else valid_hit_order
            valid_excerpt_order = None if valid_excerpt_order == 'rel' else valid_excerpt_order
            return pretty_redirect(url_for('search_form', url_query=url_query, hit_order=valid_hit_order, excerpt_order=valid_excerpt_order))
    if not url_query:
        is_bad_order, valid_hit_order, valid_excerpt_order = get_valid_order(hit_order, excerpt_order)
        if is_bad_order:
            return pretty_redirect(url_for('search_form', url_query=url_query, hit_order=valid_hit_order, excerpt_order=valid_excerpt_order))
        return render_template("search-form.html", books=Books.indexed, doc_count=ix.doc_count(), hit_order=hit_order, excerpt_order=excerpt_order)

    return search_get(url_query, hit_order, excerpt_order, url_num)


def get_valid_order(hit_order, excerpt_order):
    is_bad_hit_order = hit_order not in (None, 'rel', 'asc', 'desc')
    valid_hit_order = None if is_bad_hit_order else hit_order
    is_bad_excerpt_order = excerpt_order not in (None, 'rel', 'pos')
    valid_excerpt_order = None if is_bad_excerpt_order else excerpt_order
    is_bad_order = is_bad_hit_order or is_bad_excerpt_order
    return is_bad_order, valid_hit_order, valid_excerpt_order


def search_get(url_query, hit_order, excerpt_order, url_num):
    is_bad_order, valid_hit_order, valid_excerpt_order = get_valid_order(hit_order, excerpt_order)
    if is_bad_order:
        return pretty_redirect(url_for('search_form', url_query=url_query, hit_order=valid_hit_order, excerpt_order=valid_excerpt_order))

    # in a GET the ? is stripped, even if it's before a /, so must always use %3F for the GET url (urlize())
    # but oddly enough flask here shows it as ? even though it keeps e.g. + for spaces, so we put it back to %3F
    url_query = url_query.replace('?', '%3F')
    query = urlize(url_query, undo=True)

    weighting = AscDateBM25F if hit_order == 'asc' else DescDateBM25F if hit_order == 'desc' else BM25F
    with ix.searcher(weighting=weighting) as searcher:
        to_shorten = request.base_url.endswith('/s/')
        if to_shorten:
            return pretty_redirect(get_short_url(searcher, query, hit_order, excerpt_order))

        qp = QueryParser(default_field, my_index.search_schema)
        qp.add_plugin(DateParserPlugin())
        #todo this is pretty ugly
        try:
            qp = qp.parse(query)
        except:
            dateless_query = re.sub(r'\bdate:\[.*\]', r'', query, re.IGNORECASE)
            return pretty_redirect(url_for('search_form', url_query=urlize(dateless_query) or None, hit_order=hit_order, excerpt_order=excerpt_order, url_num=url_num))

        if isinstance(qp, type(NullQuery)):
            return pretty_redirect(url_for('search_form'))

        highlight_field = None
        for field in ('exact', 'common', 'stemmed'):
            if field + ':' in str(qp):
                highlight_field = field
                break

        is_content_search = highlight_field is not None
        if is_content_search:
            try:
                page_num = get_page_num(url_num)
            except ValueError:
                return pretty_redirect(url_for('search_form', url_query=url_query, hit_order=hit_order, excerpt_order=excerpt_order, url_num=None))
            page_results = searcher.search_page(qp, pagenum=page_num, pagelen=sessions_per_content_page)
        else:
            page_results = searcher.search_page(qp, pagenum=1, pagelen=sessions_per_listing_page)

        page_results.results.fragmenter = ParagraphFragmenter()
        page_results.results.order = highlight.FIRST if excerpt_order == 'pos' else highlight.SCORE
        page_results.results.scorer = ConsistentFragmentScorer()
        page_results.results.formatter = highlight.HtmlFormatter(between='')

        description, results = get_html_results(query, hit_order, excerpt_order, qp, page_results, highlight_field)
        pagination = get_html_pagination(url_query, hit_order, excerpt_order, page_results)
        is_content_page = highlight_field is not None
        if is_content_page and page_results.total == 1 or url_num is not None:
            # scroll = "results"
            scroll = "search"
        else:
            scroll = "search"
        return render_template("search-form.html", books=Books.indexed, doc_count=ix.doc_count(), query=query, hit_order=hit_order, excerpt_order=excerpt_order, description=description, results=results, pagination=pagination, scroll=scroll)


def get_html_pagination(url_query, hit_order, excerpt_order, page_results):
    prev_page_num = str(page_results.offset - sessions_per_content_page) if page_results.offset > sessions_per_content_page else None
    prev_url = url_for('search_form', url_query=url_query, hit_order=hit_order, excerpt_order=excerpt_order, url_num=prev_page_num)
    prev = '<a href="{}">← Previous</a>'.format(prev_url) if page_results.offset >= page_results.pagelen else ''

    next_page_num = str(page_results.offset + page_results.pagelen)
    next_url = url_for('search_form', url_query=url_query, hit_order=hit_order, excerpt_order=excerpt_order, url_num=next_page_num)
    next = '<a href="{}">Next →</a>'.format(next_url) if page_results.total - page_results.offset - page_results.pagelen > 0 else ''

    result = '{} &nbsp; {}'.format(prev, next)
    return result


def get_short_url(searcher, query, hit_order, excerpt_order):
    shorter_query = re.sub(r'\bsession:"(\d+)[^"]+"', r'session:\1', query)
    if shorter_query != query:
        qp = QueryParser(default_field, my_index.search_schema).parse(shorter_query)
        results = searcher.search(qp, limit=2)
        if results.scored_length() == 1:
            query = shorter_query
    result = url_for('search_form', url_query=urlize(query), hit_order=hit_order, excerpt_order=excerpt_order)
    result = re.sub(r'/s/$', r'/', result)
    return result


def get_page_num(url_num):
    num = int(url_num or 0)
    if num < 0:
        raise ValueError
    result = int(num / sessions_per_content_page) + 1
    return result


def get_html_results(query, hit_order, excerpt_order, qp, page_results, highlight_field):
    result = []

    is_single_page = page_results.total <= page_results.pagelen
    if is_single_page:
        result_heading = '<h2 id="results">{} result{} for {}</h2>'.format(page_results.total, 's' if page_results.total > 1 else '', qp)
    else:
        result_heading = '<h2 id="results">Results {} to {} of {} for {}</h2>'.format(page_results.offset + 1, page_results.offset + page_results.pagelen, page_results.total, qp)
    result.append(result_heading)

    description = None
    for hit_idx, hit in enumerate(page_results):
        result.append('<div class="hit">')
        result.append('<a href="javascript:void(0)" class="display-toggle" onclick="toggleDisplay(this, \'hit-{}-long\')">►</a>'.format(hit_idx))

        hit_link = get_single_result_link(hit, query, hit_order, excerpt_order)
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
            term_link = get_single_result_link(hit, key_term, hit_order, excerpt_order)
            result.append('<a class="key-term" href="{}">{}</a> '.format(term_link, key_term))
        result.append('<br />')

        result.append('<span class="hit-long" id="hit-{1}-long" style="display: none">- {0[book_name]}<br />{0[long]}</span>'.format(hit, hit_idx))

        description, highlights = get_html_highlights(highlight_field, page_results, hit_idx, hit, hit_link)
        result.extend(highlights)
        result.append("</div>")

    result = '\n'.join(result)
    return description, result


def get_html_highlights(highlight_field, page_results, hit_idx, hit, hit_link):
    description = None
    highlights = hit.highlights(highlight_field or default_field, top=single_result_paragraph_limit if page_results.total == 1 else multiple_result_paragraph_limit)

    excerpts = []
    for p_idx, cm_paragraph in enumerate(filter(None, highlights.split('\n'))):
        paragraph = commonmark(cm_paragraph).strip()

        #gets_full_paragraph = page_results.pagenum == 1 and hit_idx == 0 and p_idx < multiple_result_paragraph_limit
        gets_full_paragraph = page_results.total == 1
        if gets_full_paragraph:
            excerpts.append("<li>{}</li>".format(paragraph))
            if p_idx == 0:
                description = BeautifulSoup(paragraph, 'lxml').text.strip()
        else:
            if p_idx == multiple_result_paragraph_limit:
                excerpts.append("</ul><hr>")
                excerpts.append('<ul class="excerpts">')
            sentences = get_sentence_fragments(paragraph)
            if page_results.total > 1:
                excerpt = '<a href="{}" class="omission"> [...] </a>'.format(hit_link).join(sentences)
            else:
                excerpt = ' [...] '.join(sentences)
            excerpts.append("<li><p>{}</p></li>".format(excerpt))

    result = []
    if excerpts:
        result.append('<ul class="excerpts">')
        result.extend(excerpts)
        result.append("</ul>")
    return description, result


def get_single_result_link(hit, query, hit_order, excerpt_order):
    if hit['session']:
        session = re.sub(r'[^\w’]', ' ', hit['session'])
        session = re.sub(r'\s+', ' ', session).strip()
        session = re.sub(r'^session ', r'', session, flags=re.IGNORECASE)
        url_query = urlize('session:"{}" {}'.format(session, query), in_href=True)
        result = url_for('search_form', url_query=url_query, hit_order=hit_order, excerpt_order=excerpt_order) + 's/'
    else:
        # a bit hackish, in this case 'short' happens to be only the heading
        heading = re.sub(r'[^\w’]', ' ', hit['short'])
        heading = re.sub(r'\s+', ' ', heading).strip()
        url_query = urlize('book:{} heading:"{}" {}'.format(hit['book_abbr'].lower(), heading, query), in_href=True)
        result = url_for('search_form', url_query=url_query, hit_order=hit_order, excerpt_order=excerpt_order) + 's/'
    return result


def get_sentence_fragments(paragraph):
    paragraph_soup = BeautifulSoup(paragraph, 'lxml')

    result = []
    sentence_split = filter(None, re.split(r'(.*?(?:\.”|(?<!\b\w)(?<!\b(?:Dr|Sr|Jr|Mr|Ms))(?<!\bMrs)\.|[?!])[\s$])', paragraph))
    last_match_idx = None
    for s_idx, raw_sentence in enumerate(sentence_split):
        raw_sentence = raw_sentence.strip('\n')

        if 'class="match ' in raw_sentence:
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