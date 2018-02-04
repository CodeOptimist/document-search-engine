import os
import re
from datetime import datetime

from whoosh import index, analysis
from whoosh.analysis import StandardAnalyzer, StemmingAnalyzer, STOP_WORDS, CharsetFilter
from whoosh.fields import ID, TEXT, Schema, STORED, DATETIME
from whoosh.support.charset import accent_map

from books import Books
from mod_whoosh import CleanupStandardAnalyzer, CleanupStemmingAnalyzer


# todo manually search for and fix these where a misplaced asterisk breaks italics: \*[^*]*? \*

def pre_process_book(book, text):
    if book['abbr'] == 'DEaVF2':
        text = text.replace('\nTHE** H**ANDICAPPED**.\n\n\n\n\n',
                            '\nTHE** H**ANDICAPPED**.\n\n\n\n## **SESSION 906, MARCH 6, 1980  \n8:52 P.M. THURSDAY**\n')
    if book['abbr'] == 'TPS7':
        text = text.replace('\n***DELETED***\n', '\n***DELETED SESSION***\n')

    # strip space before ending asterisk from Markdown for CommonMark
    text = re.sub(r' \*$', r'*', text, flags=re.MULTILINE)

    # fix false bold e.g. "*hello ho**w ar**e you*" where the double asterisks are meant to close and re-open italics
    # but are (rightly) interpreted by CommonMark as bold
    # we unfortunately strip real instances of bold/underline
    while True:
        replaced_text = re.sub(r'^(\*.+?)\*\*(.+?\*)$', r'\1\2', text, flags=re.MULTILINE)
        if replaced_text == text:
            break
        text = replaced_text
    return text


def clean_header(_text):
    text = _text.replace('\(', '(').replace('\)', ')').replace('\[', '[').replace('\]', ']')
    text = re.sub('[*#>]+', '', text)
    text = re.sub(r'[ \xa0\n]+', r' ', text)
    text = text.strip()
    return text


def add_document(writer, d, tiers, content):
    assert(len(tiers) == 3)

    # everything but session
    doc_heading = []
    for tier in tiers[0:2]:
        doc_heading.append(tier['short'])
        doc_heading.append(tier['long'])
    doc_heading.append(tiers[2]['long'])
    d['heading'] = ' '.join(filter(None, doc_heading))

    # short form headings, use general if specific doesn't exist
    doc_short = [tiers[1]['short'] if tiers[1]['short'] else tiers[0]['short'],
                 tiers[2]['short']]
    d['short'] = ': '.join(filter(None, doc_short))

    # all the long form headings
    d['long'] = ''.join(["- {}<br />".format(tier['long']) for tier in tiers if tier['long']])

    d['session'] = tiers[2]['short']
    d['date'] = get_date_from_session(d['session'])
    d['key_terms_content'] = content
    print("{}\t{}\t{}".format(d['book_abbr'], d['heading'], d['session']))
    writer.add_document(**d)


last_date = None
def get_date_from_session(session):
    global last_date
    if not session:
        return None

    m = re.search(r'\b(?:january|february|march|april|may|june|july|august|september|october|november|december) \d+\b(?:, (?P<year>\d+))?', session, re.IGNORECASE)
    if not m:
        return None

    date_str = m.group(0)
    if not m.group('year'):
        # todo fix these in books.py, get rid of last_date hack
        print(last_date.date(), session)
        date_str += ", {}".format(last_date.year)

    result = datetime.strptime(date_str, '%B %d, %Y')
    last_date = result
    return result


def add_key_terms(ix):
    s = ix.searcher()
    w = ix.writer()
    stemmer = analysis.StemmingAnalyzer()

    print("Adding key terms...")
    last_book = None
    for doc_num in s.document_numbers():
        fields = s.stored_fields(doc_num)
        if fields['book_name'] != last_book:
            last_book = fields['book_name']
            print(last_book)
        m = re.search(r'session (\d+)', fields['session'], flags=re.IGNORECASE)
        is_session_num = lambda k: re.match(r'{0}(st|nd|rd|th)?'.format(m.group(1)), k) if m else False
        key_terms = [k for k, v in s.key_terms([doc_num], 'key_terms_content', numterms=10) if not is_session_num(k)]
        stemmed = [t.text for t in stemmer(' '.join(key_terms))]

        final_terms = []
        final_stemmed = set()
        for (term, stemmed_term) in zip(key_terms, stemmed):
            if stemmed_term not in final_stemmed:
                final_terms.append(term)
                final_stemmed.add(stemmed_term)

        fields['key_terms'] = final_terms
        fields['stemmed'] = fields['key_terms_content']
        fields['exact'] = fields['key_terms_content']
        fields['common'] = fields['key_terms_content']
        del fields['key_terms_content']
        w.delete_document(doc_num)
        w.add_document(**fields)
    w.commit()


def title(_text):
    text = _text
    if not re.search(r'[a-z]', text):
        text = text.title()
        text = re.sub(r'\bEsp\b', r'ESP', text)
        text = re.sub(r'\bRfb\b', r'RFB', text)
        text = re.sub(r'\bSeth Ii\b', r'Seth II', text)
        text = re.sub(r'(\d [AP])m\b', r'\1M', text)
        text = re.sub(r"(\w[’'])S\b", r'\1s', text)
    return text


def update_heading_tiers(book, tiers, header):
    # simplify things by only looking at the short part of any header substitutions we did
    short_header = header.split('\n')[0]

    for tier_idx, _ in enumerate(tiers):
        tier_re = book['tier{}'.format(tier_idx)]

        if tier_re['begin'] and re.search(tier_re['begin'], short_header, flags=re.IGNORECASE):
            short, long = header.split('\n') if '\n' in header else (header, '')
            tiers[tier_idx] = {'short': title(short), 'long': title(long)}

        if tier_re['end'] and re.search(tier_re['end'], short_header, flags=re.IGNORECASE):
            tiers[tier_idx] = {'short': '', 'long': ''}


# letters allowed, optionally interspersed with periods or asterisks, can't end with a number
# if it's only numbers then it's fine to end with a number
# term can't be adjacent to mid-line double asterisks
# (remember that our pre-processing fixed *hello ho**w are you* to *hello how are you* already, so legitimate ones are safe)
analyzer_re = re.compile(r'(?<![^\n]\*\*)\b(\w+([.*]?\w+)*(?<![0-9])|[0-9]+([.*]?[0-9]+)*)\b(?!\*\*[^\n])', re.UNICODE)
search_schema = Schema(book=ID(),
                       heading=TEXT(analyzer=StemmingAnalyzer(minsize=1, stoplist=None) | CharsetFilter(accent_map)),
                       session=TEXT(analyzer=StandardAnalyzer(minsize=1, stoplist=None)),
                       date=DATETIME(),
                       exact=TEXT(analyzer=StandardAnalyzer(stoplist=None) | CharsetFilter(accent_map)),
                       stemmed=TEXT(analyzer=StemmingAnalyzer() | CharsetFilter(accent_map)),
                       common=TEXT(analyzer=StemmingAnalyzer(stoplist=None) | CharsetFilter(accent_map)),
                       )


def create_index(index_dir):
    schema = Schema(book_abbr=STORED(),
                    book_name=STORED(),
                    book_tree=STORED(),
                    book_kindle=STORED(),
                    short=STORED(),
                    long=STORED(),
                    key_terms=STORED(),
                    key_terms_content=TEXT(stored=True, analyzer=CleanupStandardAnalyzer(analyzer_re, STOP_WORDS) | CharsetFilter(accent_map)),
                    book=ID(stored=True),
                    heading=TEXT(stored=True, analyzer=StemmingAnalyzer(minsize=1, stoplist=None) | CharsetFilter(accent_map)),
                    session=TEXT(stored=True, analyzer=StandardAnalyzer(minsize=1, stoplist=None)),
                    date=DATETIME(stored=True, sortable=True),
                    exact=TEXT(stored=True, analyzer=CleanupStandardAnalyzer(analyzer_re, stoplist=None) | CharsetFilter(accent_map)),
                    stemmed=TEXT(stored=True, analyzer=CleanupStemmingAnalyzer(analyzer_re) | CharsetFilter(accent_map)),
                    common=TEXT(stored=True, analyzer=CleanupStemmingAnalyzer(analyzer_re, stoplist=None) | CharsetFilter(accent_map)),
                    )

    ix = index.create_in(index_dir, schema)

    writer = ix.writer()
    for book in Books.indexed:
        with open("books/{}.txt".format(book['abbr']), encoding='utf-8') as f:
            text = pre_process_book(book, f.read())
        text = re.search(book['book_re'], text, flags=re.DOTALL).group(1)

        d = {
            'book_name': book['name'],
            'book_abbr': book['abbr'],
            'book_tree': book['tree'],
            'book_kindle': book['kindle'],
            'book': book['abbr'].lower(),
        }

        i = 0
        heading_tiers = [{'short': '', 'long': ''}] * 3
        carry_over_header = None
        headers = list(filter(None, book['headers_re'].split(text)[1:]))
        for (_header, _content) in zip(headers[::2], headers[1::2]):
            content = _header + _content
            if carry_over_header:
                content = carry_over_header + content
                carry_over_header = None

            header = clean_header(_header)
            if 'header_replaces' in book:
                for (pattern, repl) in book['header_replaces']:
                    header = pattern.sub(repl, header, 1)

            update_heading_tiers(book, heading_tiers, header)

            has_content = re.search(r'[a-z]', _content)
            if not has_content:
                carry_over_header = content
                continue

            add_document(writer, d, heading_tiers, content)
            i += 1
        print(i)

    writer.commit()
    return ix


def create_index_and_key_terms(index_dir):
    if not os.path.isdir(index_dir):
        os.mkdir(index_dir)

    ix = create_index(index_dir)
    add_key_terms(ix)
    return ix
