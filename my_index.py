import re

from whoosh import index, analysis
from whoosh.fields import ID, TEXT, Schema, STORED

from books import Books


def pre_process_book(book, text):
    if book['abbr'] == 'DEaVF2':
        text = text.replace('\nTHE** H**ANDICAPPED**.\n\n\n\n\n',
                            '\nTHE** H**ANDICAPPED**.\n\n\n\n## **SESSION 906, MARCH 6, 1980  \n8:52 P.M. THURSDAY**\n')

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
    text = _text.replace('\(', '(').replace('\)', ')')
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
    d['key_term_content'] = content
    print(d['book_abbr'], d['heading'], d['session'])
    writer.add_document(**d)


def add_key_terms(ix):
    s = ix.searcher()
    w = ix.writer()
    stemmer = analysis.StemmingAnalyzer()

    for doc_num in s.document_numbers():
        fields = s.stored_fields(doc_num)
        key_terms = [k for k, v in s.key_terms([doc_num], 'key_term_content', numterms=10)]
        stemmed = [t.text for t in stemmer(' '.join(key_terms))]

        final_terms = []
        final_stemmed = set()
        for (term, stemmed_term) in zip(key_terms, stemmed):
            if stemmed_term not in final_stemmed:
                final_terms.append(term)
                final_stemmed.add(stemmed_term)

        fields['key_terms'] = final_terms
        fields['content'] = fields['key_term_content']
        del fields['key_term_content']
        w.delete_document(doc_num)
        w.add_document(**fields)
    w.commit()


def title(_text):
    text = _text
    if not re.search(r'[a-z]', text):
        text = text.title()
        text = re.sub(r'\bEsp\b', r'ESP', text)
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


def create_index(index_dir):
    # letters interspersed with periods or asterisks allowed but can't end with a number
    # and term can't be adjacent to double asterisks
    key_term_re = r'(?<!\*\*)\b\w+([.*]?\w+)*(?<![0-9])\b(?!\*\*)'
    schema = Schema(book_abbr=STORED(),
                    book_name=STORED(),
                    book_tree=STORED(),
                    book_kindle=STORED(),
                    short=STORED(),
                    long=STORED(),
                    key_terms=STORED(),
                    key_term_content=TEXT(stored=True, analyzer=analysis.StandardAnalyzer(re.compile(key_term_re, re.UNICODE))),
                    book=ID(stored=True),
                    heading=TEXT(stored=True, analyzer=analysis.StemmingAnalyzer(minsize=1, stoplist=None)),
                    session=TEXT(stored=True, analyzer=analysis.StandardAnalyzer(minsize=1, stoplist=None)),
                    content=TEXT(stored=True, analyzer=analysis.StemmingAnalyzer()))

    ix = index.create_in(index_dir, schema)

    writer = ix.writer()
    for book in Books.indexed:
        with open("books/{}.txt".format(book['abbr']), encoding='utf-8') as f:
            text = pre_process_book(book, f.read())

        d = {
            'book_name': book['name'],
            'book_abbr': book['abbr'],
            'book_tree': book['tree'],
            'book_kindle': book['kindle'],
            'book': book['abbr'].lower(),
        }

        heading_tiers = [{'short': '', 'long': ''}] * 3
        carry_over_header = None
        headers = list(filter(None, book['headers_re'].split(text)[1:-2]))
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

    writer.commit()
    return ix


def create_index_and_key_terms(index_dir):
    ix = create_index(index_dir)
    add_key_terms(ix)
    return ix
