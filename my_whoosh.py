from whoosh.analysis import RegexTokenizer, LowercaseFilter, StopFilter, STOP_WORDS, default_pattern, Filter, StemFilter, stem
from whoosh.highlight import Fragmenter, Fragment, BasicFragmentScorer


class ParagraphFragmenter(Fragmenter):
    def fragment_matches(self, text, matched_tokens):
        pass

    def fragment_tokens(self, text, tokens):
        paragraph_tokens = []
        last = (None, None)

        for t in tokens:
            if t.matched:
                cur = self.get_paragraph_pos(text, t)
        #
                if cur != last and paragraph_tokens:
                    yield Fragment(text, paragraph_tokens, last[0], last[1])
                    paragraph_tokens = []
                paragraph_tokens.append(t.copy())
                last = cur

        if paragraph_tokens:
            yield Fragment(text, paragraph_tokens, last[0], last[1])

    @staticmethod
    def get_paragraph_pos(text, t):
        try:
            paragraph_start = text[:t.startchar].rindex('\n')
        except ValueError:
            paragraph_start = 0
        try:
            paragraph_end = t.endchar + text[t.endchar:].index('\n')
        except ValueError:
            paragraph_end = len(text)
        return paragraph_start, paragraph_end


class ConsistentFragmentScorer(BasicFragmentScorer):
    def __call__(self, f):
        score = super(ConsistentFragmentScorer, self).__call__(f)

        # line = f.text[f.startchar:f.endchar]
        if f.startchar:
            score += 1 / f.startchar
        return score


def CleanupStandardAnalyzer(expression=default_pattern, stoplist=STOP_WORDS, minsize=2, maxsize=None, gaps=False):
    ret = RegexTokenizer(expression=expression, gaps=gaps)
    # added CleanupFilter here
    chain = ret | CleanupFilter() | LowercaseFilter()
    if stoplist is not None:
        chain = chain | StopFilter(stoplist=stoplist, minsize=minsize, maxsize=maxsize)
    return chain


def CleanupStemmingAnalyzer(expression=default_pattern, stoplist=STOP_WORDS,
                     minsize=2, maxsize=None, gaps=False, stemfn=stem,
                     ignore=None, cachesize=50000):

    ret = RegexTokenizer(expression=expression, gaps=gaps)
    # added CleanupFilter here
    chain = ret | CleanupFilter() | LowercaseFilter()
    if stoplist is not None:
        chain = chain | StopFilter(stoplist=stoplist, minsize=minsize, maxsize=maxsize)
    return chain | StemFilter(stemfn=stemfn, ignore=ignore, cachesize=cachesize)


class CleanupFilter(Filter):
    def __call__(self, tokens):
        for t in tokens:
            t.text = t.text.replace(r'*', '')
            yield t
