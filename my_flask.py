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
from my_whoosh import ParagraphFragmenter, ConsistentFragmentScorer, DescDateBM25F, AscDateBM25F, get_sentence_fragments

app = Flask(__name__)
# occasionally a single session straddles 2 chapters, which are different hits
MAXIMUM_SAME_SESSION_HITS = 2
HITS_PER_CONTENT_PAGE = 10
HITS_PER_LISTING_PAGE = 150
MULTIPLE_HIT_EXCERPT_LIMIT = 3
SINGLE_HIT_EXCERPT_LIMIT = 50    # effectively ALL of them, I would think
SINGLE_HIT_COPY_EXCERPT_LIMIT = 10
EXCERPT_OMISSION_THRESHOLD = max(SINGLE_HIT_EXCERPT_LIMIT, MULTIPLE_HIT_EXCERPT_LIMIT)
DEFAULT_FIELD = 'stemmed'
state = {}
url_state = {}


@app.template_filter('volumes_link')
def get_html_book_link(tpl):
    abbr, name = tpl
    return """<a href="javascript:void()" title="{1}" onclick="filterBook('{0}')">{0}</a>""".format(abbr, html.escape(name))


@app.template_filter('book_link')
def book_link(book):
    return get_html_book_link((book['abbr'], book['name']))


def computed_hit_order(of='default'):
    val = url_state['hit_order'] if of == 'default' else of
    if val is None:
        return 'rel'
    return val


def computed_excerpt_order(of='default'):
    val = url_state['excerpt_order'] if of == 'default' else of
    if val is None and 'result_type' in state:
        return 'pos' if state['result_type'] == 'single' else 'rel'
    return val


@app.context_processor
def template_functions():
    def example(q, desc):
        return '<a href="/q/{}/" title="{}">{}</a>'.format(urlize(q, in_href=True), html.escape(desc), q)
    return dict(example=example, computed_hit_order=computed_hit_order, computed_excerpt_order=computed_excerpt_order)


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
        s = s.strip()
        # let's make book names lowercase
        s = re.sub(r'\bbook:(\w+)', lambda m: m.group(0).lower(), s)
        # single quote to space is how Whoosh itself treats apostrophes
        # double quotation marks to singles because in many browsers " will appear as the ugly %22
        s = s.replace("'", ' ').replace('“', '"').replace('”', '"').replace('"', '\'')
        # valid path component chars are: ()':* http://stackoverflow.com/a/2375597/879
        # but browsers seem okay with []{} also
        safe = '[]{}\'()*:'
        if not in_href:
            safe += '"'
        s = urllib.parse.quote_plus(s, safe)
    # print("End: {}".format(s))
    return s


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
    state.clear()
    url_state.update(locals().copy())

    # redirect POST to GET
    if request.method == 'POST':
        q_query = urlize(request.form['query'])
        _, hit_order, excerpt_order = get_valid_order(request.form['hit-order'], request.form['excerpt-order'])
        hit_order = hit_order if 'explicit-hit-order' in request.form else None
        excerpt_order = excerpt_order if 'explicit-excerpt-order' in request.form else None
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
        query_str = urlize(url_state['q_query'], undo=True)
        return search_whoosh(query_str)


def get_valid_order(hit_order, excerpt_order):
    valid_ho = hit_order if hit_order in (None, 'rel', 'asc', 'desc') else None
    valid_eo = excerpt_order if excerpt_order in (None, 'rel', 'pos') else None
    was_bad = valid_ho != hit_order or valid_eo != excerpt_order
    return was_bad, valid_ho, valid_eo


def get_valid_num(num):
    if num is None:
        return False, None
    try:
        num = int(num or 0)
        if num < 0:
            raise ValueError
        result = int(num / HITS_PER_CONTENT_PAGE) + 1
        return False, result
    except ValueError:
        return True, None


def search_whoosh(query_str):
    weighting = AscDateBM25F if computed_hit_order() == 'asc' else DescDateBM25F if computed_hit_order() == 'desc' else BM25F
    with ix.searcher(weighting=weighting) as searcher:
        to_session = request.base_url.endswith('/s/')
        if to_session:
            return pretty_redirect(get_optimal_session_url(searcher, query_str))

        qp = QueryParser(DEFAULT_FIELD, my_index.search_schema)
        qp.add_plugin(DateParserPlugin())
        # todo this is pretty ugly
        try:
            qp = qp.parse(query_str)
        except:
            dateless_query = re.sub(r'\bdate:\[.*\]', r'', query_str, re.IGNORECASE)
            return stateful_redirect('search_form', q_query=urlize(dateless_query) or None)

        if isinstance(qp, type(NullQuery)):
            return stateful_redirect('search_form', q_query=None)

        highlight_field = None
        for field in ('exact', 'common', 'stemmed'):
            if field + ':' in str(qp):
                highlight_field = field
                break

        if highlight_field is None:
            page_results = searcher.search_page(qp, pagenum=1, pagelen=HITS_PER_LISTING_PAGE)
            state['result_type'] = 'listing'
        else:
            page_results = searcher.search_page(qp, pagenum=url_state['page_num'] or 1, pagelen=HITS_PER_CONTENT_PAGE)
            state['result_type'] = 'single' if all_same_session(page_results) else 'multiple'

        if remove_redundant_sorting():
            return stateful_redirect('search_form')

        og_description = ""
        try:
            result = {
                'results': get_html_results(query_str, qp, page_results, highlight_field),
                'correction': get_html_correction(searcher, query_str, qp),
                'description': og_description,
                'pagination': get_html_pagination(page_results),
            }
            result['scroll'] = None if result['correction'] else "results"
        except DocumentCopy:
            return stateful_redirect('search_form', excerpt_order='rel')

        return render_template("search-form.html", **url_state, **result, query_str=query_str, books=Books.indexed, doc_count=ix.doc_count())


# noinspection PyTypeChecker
def remove_redundant_sorting():
    same_as_none = url_state['hit_order'] == computed_hit_order(None)
    no_effect = url_state['hit_order'] is not None and state['result_type'] == 'single'
    remove_hit = same_as_none or no_effect

    same_as_none = url_state['excerpt_order'] == computed_excerpt_order(None)
    no_effect = url_state['excerpt_order'] is not None and state['result_type'] == 'listing'
    remove_excerpt = same_as_none or no_effect

    url_state['hit_order'] = None if remove_hit else url_state['hit_order']
    url_state['excerpt_order'] = None if remove_excerpt else url_state['excerpt_order']
    return remove_hit or remove_excerpt


def get_html_correction(searcher, query_str, qp):
    exact_qp = QueryParser('exact', my_index.search_schema)
    exact_qp.add_plugin(DateParserPlugin())
    exact_qp = exact_qp.parse(query_str)
    try:
        corrected_query = searcher.correct_query(exact_qp, query_str, prefix=1)
    except:
        return ""

    for token in corrected_query.tokens:
        # is this some sort of bug with Whoosh? startchar:8, endchar:9 original:'tes?' the hell?
        if query_str[token.startchar:token.endchar] != token.original:
            return ""
        for variations in (uk_variations, us_variations):
            if token.original in variations and searcher.ixreader.frequency('exact', variations[token.original]) > 0:
                token.text = variations[token.original]
                break
        # not sure this code ever gets a chance to run due to above possible bug
        if re.search(r'\W', token.original):
            token.text = token.original
    corrected_query_str = replace_tokens(query_str, corrected_query.tokens)
    corrected_qp = QueryParser('stemmed', my_index.search_schema)
    corrected_qp.add_plugin(DateParserPlugin())
    corrected_qp = corrected_qp.parse(corrected_query_str)
    if corrected_qp == qp:
        return ""

    result = '<h3>Did you mean <a href="{}">{}</a>?</strong></h3>'.format(
        stateful_url_for('search_form', q_query=urlize(corrected_query_str)),
        corrected_query.format_string(highlight.HtmlFormatter(classname="change")))
    return result


def replace_tokens(text, tokens):
    if not tokens:
        return text

    result = ""
    endchar = 0
    for t in tokens:
        result += text[endchar:t.startchar] + t.text
        endchar = t.endchar
    result += text[endchar:]
    return result


def get_html_pagination(page_results):
    prev_page_num = str(page_results.offset - HITS_PER_CONTENT_PAGE) if page_results.offset > HITS_PER_CONTENT_PAGE else None
    prev_url = stateful_url_for('search_form', page_num=prev_page_num)
    prev = '<a href="{}">← Previous</a>'.format(prev_url) if page_results.offset >= page_results.pagelen else ''

    next_page_num = str(page_results.offset + page_results.pagelen)
    next_url = stateful_url_for('search_form', page_num=next_page_num)
    next = '<a href="{}">Next →</a>'.format(next_url) if page_results.total - page_results.offset - page_results.pagelen > 0 else ''

    result = '{} &nbsp; {}'.format(prev, next)
    return result


def get_optimal_session_url(searcher, query_str):
    hit_order = None
    shorter_query = re.sub(r'\bsession:"(\d+)[^"]+"', r'session:\1', query_str)
    qp = QueryParser(DEFAULT_FIELD, my_index.search_schema).parse(shorter_query)
    # the limit is purely for efficiency
    results = searcher.search(qp, limit=MAXIMUM_SAME_SESSION_HITS + 1)
    if all_same_session(results):
        if results.scored_length() > 1:
            hit_order = 'asc'  # so we can see sessions that span chapters in order
        query_str = shorter_query
    result = stateful_url_for('search_form', q_query=urlize(query_str), hit_order=hit_order, page_num=None)
    result = re.sub(r'/s/$', r'/', result)
    return result


def all_same_session(results):
    result = all(results[0]['session'] == hit['session'] for hit in results)
    return result


def get_html_coverage(hit, highlights):
    full_doc_text = hit['exact']
    num_highlight_paragraphs = highlights.count('\n')
    num_doc_paragraphs = full_doc_text.count('\n\n')
    coverage = num_highlight_paragraphs / num_doc_paragraphs

    is_copy = False
    if state['result_type'] == 'single' and computed_excerpt_order() == 'pos':
        if len(full_doc_text) > 1500 and (coverage > 0.5 or num_highlight_paragraphs == SINGLE_HIT_EXCERPT_LIMIT):
            is_copy = True

    if is_copy:
        num_highlight_paragraphs = min(num_highlight_paragraphs, SINGLE_HIT_COPY_EXCERPT_LIMIT)
        coverage = num_highlight_paragraphs / num_doc_paragraphs
    html_coverage = '<span class="coverage" title="excerpts/paragraphs">{}/{} ({}%)</span>'.format(
        num_highlight_paragraphs, num_doc_paragraphs, round(coverage * 100))
    result = is_copy, html_coverage
    return result


def get_html_results(query_str, qp, page_results, highlight_field):
    result = ""
    is_single_page = page_results.total <= page_results.pagelen
    if is_single_page:
        heading = '<h2 id="results">{} result{} for {}</h2>\n'.format(page_results.total, 's' if page_results.total > 1 else '', qp)
    else:
        heading = '<h2 id="results">Results {} to {} of {} for {}</h2>\n'.format(page_results.offset + 1,
                                                                                 page_results.offset + page_results.pagelen, page_results.total, qp)
    result += heading

    page_results.results.fragmenter = ParagraphFragmenter()
    page_results.results.order = highlight.FIRST if computed_excerpt_order() == 'pos' else highlight.SCORE
    page_results.results.scorer = ConsistentFragmentScorer()
    page_results.results.formatter = highlight.HtmlFormatter(between='')

    result += '<div class="{}">'.format(state['result_type'])
    for hit_idx, hit in enumerate(page_results):
        html_hit = get_html_hit(query_str, highlight_field, page_results, hit_idx, hit)
        result += html_hit
    result += '</div>'

    if state['result_type'] == 'single' or page_results.total == 1:
        more_like = get_html_more_like(page_results)
        result += more_like
    return result


class DocumentCopy(Exception):
    pass


def get_html_hit(query_str, highlight_field, page_results, hit_idx, hit):
    result = '<div class="hit">\n'

    html_hit_link = get_single_session_url(query_str, hit)
    html_hit_heading = get_html_hit_heading(state['result_type'], "hit-{}".format(hit_idx), hit, html_hit_link)

    html_excerpts = ""
    if state['result_type'] in ('single', 'multiple'):
        limit = SINGLE_HIT_EXCERPT_LIMIT if state['result_type'] == 'single' else MULTIPLE_HIT_EXCERPT_LIMIT + 1
        highlights = hit.highlights(highlight_field or DEFAULT_FIELD, top=limit)
        is_copy, html_coverage = get_html_coverage(hit, highlights)
        if state['result_type'] == 'single':
            html_hit_heading = html_hit_heading.replace('<!--coverage-->', html_coverage)
        if is_copy:
            is_ordered_implicitly = url_state['excerpt_order'] is None
            if is_ordered_implicitly:
                raise DocumentCopy
            result += """<h4>These excerpts have been limited due to closely matching the copyrighted work.<br />
            For full results please <a onclick="document.getElementById('excerpt-order-rel').click();document.getElementById('submit').click();"
             href="javascript:void(0);">sort excerpts by relevance</a>, or narrow your search.</h4>\n"""
        html_excerpts = get_html_excerpts(page_results, hit_idx, html_hit_link, highlights, is_copy)

    result += html_hit_heading
    result += html_excerpts
    result += '</div>\n'
    return result


def get_html_hit_heading(result_type, hit_id, hit, html_hit_link):
    result = '<a href="javascript:void(0)" class="display-toggle" onclick="toggleDisplay(this, \'{}-long\')">►</a>\n'.format(hit_id)

    if result_type == 'multiple':
        result += '<a href="{1}" class="heading">{0[book_abbr]} {0[short]}</a>\n'.format(hit, html_hit_link)
    elif result_type in ('single', 'listing'):
        result += '<span class="heading">{0[book_abbr]} {0[short]}</span>\n'.format(hit)
    else:
        raise AssertionError

    icon = re.sub(r'(tes|tps|tecs)\d', r'\1', hit['book_abbr'].lower())
    result += '<span class="icons">\n'
    result += '<a href="{0[book_tree]}" class="book-link" target="_blank"><img src="/static/{1}.png"/></a>\n'.format(hit, icon)
    result += '<a href="{0[book_kindle]}" class="kindle-link" target="_blank"><img src="/static/kindle.png"/></a>\n'.format(hit)
    result += '</span>\n'

    result += '<!--coverage-->\n'

    result += '<span class="terms">\n'
    for key_term in hit['key_terms'][:5]:
        term_link = get_single_session_url(key_term, hit)
        result += '<a class="key-term" href="{}">{}</a> \n'.format(term_link, key_term)
    result += '</span>\n'

    result += '<br />\n<span class="hit-long" id="{1}-long" style="display: none">- {0[book_name]}<br />{0[long]}</span>\n'.format(hit, hit_id)
    return result


def get_html_more_like(results):
    try:
        if results.total == 1:
            similar_results = results[0].searcher.more_like(results[0].docnum, 'exact', top=5)
        else:
            text = ''.join(h['exact'] for h in results)
            similar_results = results[0].searcher.more_like(None, 'exact', text=text, top=5)
    except:
        return ""

    result = '<div class="similar">\n'
    result += '<h2>Similar sessions</h2>\n'
    for hit_idx, hit in enumerate(similar_results):
        result += '<div class="similar-hit">\n'
        heading = get_html_hit_heading('listing', 'similar-{}'.format(hit_idx), hit, None)
        result += heading
        result += '</div>\n'
    result += '</div>\n'
    return result


def get_html_excerpts(page_results, hit_idx, hit_link, highlights, is_copy):
    global og_description
    result = '<ul class="excerpts">\n'
    for p_idx, cm_paragraph in enumerate(filter(None, highlights.split('\n'))):
        if is_copy and p_idx == SINGLE_HIT_COPY_EXCERPT_LIMIT:
            break

        if state['result_type'] == 'multiple' and p_idx == MULTIPLE_HIT_EXCERPT_LIMIT:
            result += '<li><p><a href="{}"> More... </a></p></li>\n'.format(hit_link)
            continue

        paragraph = commonmark(cm_paragraph).strip()

        if hit_idx == 0 and p_idx == 0:
            update_og_description(page_results.total, paragraph)

        is_first_hit_preview = page_results.pagenum == 1 and hit_idx == 0 and p_idx < EXCERPT_OMISSION_THRESHOLD
        gets_full_paragraph = (state['result_type'] == 'single' or is_first_hit_preview) and not is_copy
        if gets_full_paragraph:
            result += '<li>{}</li>\n'.format(paragraph)
        else:
            if p_idx == EXCERPT_OMISSION_THRESHOLD:
                result += '</ul>\n<hr>\n<ul class="excerpts">\n'
            sentences = get_sentence_fragments(paragraph)
            if state['result_type'] == 'single':
                excerpt = ' [...] '.join(sentences)
            else:
                excerpt = '<a href="{}" class="omission"> [...] </a>'.format(hit_link).join(sentences)
            result += '<li><p>{}</p></li>\n'.format(excerpt)

    result += '</ul>\n'
    return result


def update_og_description(num_results, paragraph):
    global og_description
    og_description = BeautifulSoup(paragraph, 'lxml').text.strip()
    if num_results > 1:
        og_description = "{} results.  {}".format(num_results, og_description)


def get_single_session_url(query_str, hit):
    if hit['session']:
        session = re.sub(r'[^\w’]', ' ', hit['session'])
        session = re.sub(r'\s+', ' ', session).strip()
        session = re.sub(r'^session ', r'', session, flags=re.IGNORECASE)
        q_query = urlize('session:"{}" {}'.format(session, query_str), in_href=True)
        result = stateful_url_for('search_form', q_query=q_query, hit_order=None, excerpt_order=None, page_num=None) + 's/'
    else:
        # a bit hackish, in this case 'short' happens to be only the heading
        heading = re.sub(r'[^\w’]', ' ', hit['short'])
        heading = re.sub(r'\s+', ' ', heading).strip()
        q_query = urlize('book:{} heading:"{}" {}'.format(hit['book_abbr'].lower(), heading, query_str), in_href=True)
        result = stateful_url_for('search_form', q_query=q_query, hit_order=None, excerpt_order=None, page_num=None) + 's/'
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


def load_uk_us_variations():
    for line in open(r'uk_us_variations.txt', encoding='utf-8', mode='r').readlines():
        uk, us = line.strip().split(' ')
        uk_variations[uk] = us
        us_variations[us] = uk
        uk_us_variations.add(uk)
        uk_us_variations.add(us)


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


og_description = ""
uk_variations = {}
us_variations = {}
uk_us_variations = set()
load_uk_us_variations()
main()