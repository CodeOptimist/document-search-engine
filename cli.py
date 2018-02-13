import argparse
import os
import sys
import my_index


def test(ix):
    from whoosh.query import Every
    results = ix.searcher().search(Every('session'), limit=None)
    for result in results:
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--interactive", help="load search index interactively", action='store_true')
    parser.add_argument("-r", "--rebuild", help="rebuild index", nargs='?', const="index")
    parser.add_argument("-t", "--test", help="test", action='store_true')
    args = parser.parse_args()

    if args.rebuild:
        my_index.new_index(args.rebuild)
    else:
        os.chdir(sys.path[0])
        ix = my_index.get_idx('index')
        if args.test:
            test(ix)


if __name__ == '__main__':
    main()