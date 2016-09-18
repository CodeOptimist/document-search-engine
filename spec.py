import re
from books import Books

from whoosh.fields import ID, TEXT, Schema, STORED
from whoosh import index, analysis


def clean(_text):
    text = _text.replace('\(', '(').replace('\)', ')')
    text = re.sub('[\*\#>]+', '', text)
    text = re.sub(r'[ \xa0\n]+', r' ', text)
    text = text.strip()

    # text = re.sub(r'[\*\#>]+', '', text).strip()
    return text


def title(_text):
    text = _text
    if not re.search(r'[a-z]', text):
        text = text.title()
        text = re.sub(r'\bEsp\b', r'ESP', text)
    return text


def create_index(indexdir):
    schema = Schema(book_abbr=STORED(),
                    book_name=STORED(),
                    book_url=STORED(),
                    short=STORED(),
                    long=STORED(),
                    book=ID(stored=True),
                    title=TEXT(stored=True, analyzer=analysis.StemmingAnalyzer(minsize=1, stoplist=None)),
                    id=TEXT(stored=True, analyzer=analysis.StandardAnalyzer(minsize=1, stoplist=None)),
                    content=TEXT(stored=True, analyzer=analysis.StemmingAnalyzer()))

    ix = index.create_in(indexdir, schema)
    writer = ix.writer()

    for book in Books.indexed:
        with open("books/{}.txt".format(book['abbr']), encoding='utf-8') as f:
            text = f.read()

        d = {
            'book_name': book['name'],
            'book_abbr': book['abbr'],
            'book_url': book['url'],
            'book': book['abbr'].lower(),
        }

        tiers = [('', '')] * 3
        carry_over_header = None
        headers = list(filter(None, book['headers_re'].split(text)[1:-2]))
        for (_header, _content) in zip(headers[::2], headers[1::2]):
            content = _header + _content
            if carry_over_header:
                content = carry_over_header + content
                carry_over_header = None

            header = clean(_header)
            if 'header_replaces' in book:
                for (pattern, repl) in book['header_replaces']:
                    header = pattern.sub(repl, header, 1)

            get_tiers(book, tiers, header)
            has_content = re.search(r'[a-z]', _content)

            if not has_content:
                carry_over_header = content
                continue

            add_document(writer, d, tiers, content)

    writer.commit()
    return ix


def get_tiers(book, tiers, header):
    short_header = header.split('\n')[0]
    for tier_idx in range(3):
        tier_start, tier_end = book['tier{}'.format(tier_idx)]
        if tier_start and tier_start.search(short_header):
            if '\n' in header:
                short, long = header.split('\n')
            else:
                short, long = header, ''
            tiers[tier_idx] = (title(short), title(long))

        if tier_end and tier_end.search(short_header):
            tiers[tier_idx] = ('', '')


def add_document(writer, d, tiers, content):
    d['id'] = tiers[2][0]
    d['title'] = ' '.join([tiers[i][j] for i in range(3) for j in range(2) if (i, j) != (2, 0) and tiers[i][j]])
    d['content'] = content

    start = 1 if tiers[1][0] else 0
    d['short'] = ': '.join([tiers[i][0] for i in range(start, 3) if tiers[i][0]])
    d['long'] = ''.join(["- {}<br />".format(tiers[i][1]) for i in range(3) if tiers[i][1]])
    print(d['book_abbr'], d['title'], d['id'])
    writer.add_document(**d)

