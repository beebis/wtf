#!/usr/bin/env python2
import argparse
from sys import stdin, stdout, stderr, exit
import re
import os
import shutil
try:
    import cStringIO as StringIO
except ImportError:
    import StringIO

class slurpy(dict):
	def __getattr__(self, k):
		return self[k]
	def __setattr__(self, k, v):
		self[k]=v

def multi_opt(p, *args, **kw):
    values = kw.pop('values', ('fix','report',None))
    longs = kw.pop('longs', ('','report-','ignore-'))
    shorts = kw.pop('shorts', ('', lambda s: s.upper() if s.upper()!=s else s.lower() if s.lower()!=s else s, 'I'))

    g=p.add_mutually_exclusive_group(required=False)

    d = dict(kw)
    d['action']='store_const'

    for v, l, s in zip(values, longs, shorts):
        d['const'] = v

        # mangle the flags, e.g.
        #   '-a', '--allow' becomes '-A', '--no-allow'
        a2 = []
        for a in args:
            if a.startswith('--'):
                if hasattr(l, '__call__'):
                    a2.append('--%s' % (l(a[2:])))
                else:
                    a2.append('--%s' % (l+a[2:]))
            elif a.startswith('-'):
                if hasattr(s, '__call__'):
                    a2.append('-%s' % s(a[1:]))
                else:
                    a2.append('-%s' % (s+a[1:]))
            else:
                a2.append(a)

        opt = g.add_argument(*a2, **d)
        d.setdefault('dest', opt.dest)

        # no help or dest after first option
        if 'help' in d: del d['help']
        if 'default' in d: del d['default']

    return g

# Parse arguments
p = argparse.ArgumentParser(description='''
    Whitespace Total Fixer. Removes all manner of annoying whitespace errors from text files.
    Exit codes on successful operation:
        0: no errors seen
        10: errors fixed
        20: unfixed errors seen''')
g=p.add_argument_group("Input/output modes")
g.add_argument('inf', metavar="textfile", nargs='*', type=argparse.FileType('rb'), help='Input file(s)', default=[stdin])
g.add_argument('-o', metavar="outfile", dest='outf', type=argparse.FileType('w'), help='Output file (default is stdout)', default=stdout)
g2=g.add_mutually_exclusive_group(required=False)
g2.add_argument('-i', dest='inplace', action='store_const', const=True, help='In-place editing; allows multiple input files')
g2.add_argument('-I', dest='inplace', metavar='.EXT', help='Same, but makes backups with specified extension')

g=p.add_argument_group("Trailing space")
multi_opt(g, '-t', '--trail-space', default='fix', help='Remove space at end-of-line (default %(default)s)')

g=p.add_argument_group("End of file")
multi_opt(g, '-b', '--eof-blanks', default='fix', help='Remove blank lines at end-of-file (default %(default)s)')
multi_opt(g, '-n', '--eof-newl', default='fix', help='Ensure newline appears at end-of-file (default %(default)s)')

g=p.add_argument_group("End of line characters")
multi_opt(g, '-m', '--match-eol', default='fix', help='Make sure all lines match the first line (default %(default)s)')
g.add_argument('-E', '--coerce-eol', action='store', metavar='ENDING', choices=('crlf','lf','native','none'), default='none',
               help='Coerce line endings to a specific type: crlf, lf, or native (default %(default)s)');

g=p.add_argument_group("Tabs")
multi_opt(g, '-s', '--tab-space-mix', default='warn', help='Warn if spaces followed by tabs in whitespace at beginning of line (default %(default)s)',
          values=('report','ignore'), longs=('','ignore-'),shorts=('','I'))

p.add_argument('-v', '--verbose', action='count', default=0, help="Increasing verbosity")
p.add_argument('-X', '--no-exit-codes', action='store_true', help="Always return 0 on success")
args = p.parse_args()

# Check for things that don't make sense
if args.inplace is not None and stdin in args.inf:
    p.error("cannot use stdin for in-place editing (-i/-I); must specify filenames")
elif args.outf!=stdout:
    if args.inplace is not None:
        p.error("cannot specify both in-place editing (-i/-I) and output file (-o)")
    elif len(args.inf)>1:
        p.error("cannot specify multiple input files with a single output file (-o)")

# Actions that we're going to do
actions = slurpy((k,getattr(args,k)) for k in ('trail_space','eof_blanks','eof_newl','match_eol','coerce_eol','tab_space_mix'))
coerce_eol = dict(crlf='\r\n',lf='\n',native=os.linesep,none=None)[args.coerce_eol]
all_seen = 0
all_fixed = 0

lre = re.compile(r'''
    (?P<ispace>\s*?)
    (?P<body>(?:\S.*?)?)
    (?P<trail>\s*?)
    (?P<eol>(?:\r\n|\n|\r|))$''', re.S | re.X)

# Process files
for inf in args.inf:
    # use in-memory copy for inplace
    if args.inplace is not None:
        fname = inf.name
        orig = inf
        inf = StringIO.StringIO(inf.read())
        orig.close()
        # make backup if desired
        if isinstance(args.inplace, basestring):
            try:
                shutil.copyfile(fname, fname + args.inplace)
            except shutil.Error:
                p.error("could not make backup copy of %s as %s" % (fname, fname + args.inplace))
        outf = open(fname, "wb")
    else:
        outf = args.outf
        if inf is stdin and outf is not stdout:
            fname = outf.name
        else:
            fname = inf.name

    buffer = []
    seen = slurpy((k,0) for k in actions)
    fixed = slurpy((k,0) for k in actions)
    first_eol = linesep = None

    for ii,line in enumerate(inf):
        # Take the line apart, and save first EOL for matching subsequent lines to it
        m = lre.match(line)
        ispace, body, trail, eol = m.groups()
        empty = not body
        if first_eol is None:
            first_eol = eol
            linesep = {'\r\n':'crlf','\n':'lf'}.get(eol, repr(eol))

        if args.verbose>=4:
            print>>stderr, "%s %sLINE %d: %s" % (fname, 'EMPTY ' if empty else '', ii+1, repr(m.groups()))

        # Warn about tab/space mix
        if actions.tab_space_mix:
            if ' \t' in ispace:
                seen.tab_space_mix += 1
                if actions.tab_space_mix=='report':
                    print>>stderr, "%s %sLINE %d: WARNING: spaces followed by tabs in whitespace at beginning of line" % (fname, 'EMPTY ' if empty else '', ii+1)

        # Fix trailing space
        if actions.trail_space:
            if trail:
                seen.trail_space += 1
                if actions.trail_space=='fix':
                    fixed.trail_space += 1
                    trail = ''

        # Line endings (missing, matching, and coercing)
        if not eol:
            # there is no line ending...
            if actions.eof_newl:
                seen.eof_newl += 1
                if actions.eof_newl=='fix':
                    # ... but we want one
                    fixed.eof_newl += 1
                    if coerce_eol:
                        eol = coerce_eol
                    elif first_eol:
                        eol = first_eol
                    else:
                        eol = os.linesep
                        linesep = {'\r\n':'crlf','\n':'lf'}.get(eol, repr(eol))
                        print>>stderr, "%s %sLINE %d: WARNING: don't know what line ending to add (guessed %s)" % (fname, 'EMPTY ' if empty else '', ii+1, linesep)
        else:
            # there is a line ending...
            if eol!=first_eol:
                if actions.match_eol:
                    # ... but it doesn't match first line
                    seen.match_eol += 1
                    if actions.match_eol=='fix':
                        fixed.match_eol += 1
                        eol = first_eol

            # Line endings (coercing)
            if eol!=coerce_eol and coerce_eol:
                # ... but it isn't the one we want to force
                eol = coerce_eol
                seen.coerce_eol += 1
                fixed.coerce_eol += 1

        # Put the line back together
        outline = ispace+body+trail+eol
        if args.verbose>=3 and outline!=line:
            print>>stderr, "%s %sLINE %d: changing %s to %s" % (fname, 'EMPTY ' if empty else '', ii+1, repr(line), repr(outline))

        # empty line, could be at end of file
        if empty:
            buffer.append(outline)
        else:
            if buffer:
                outf.write(''.join(buffer))
                buffer = []
            outf.write(outline)

    # handle blank lines at end
    if buffer:
        # we have leftover lines ...
        if actions.eof_blanks:
            seen.eof_blanks += len(buffer)
            if actions.eof_blanks=='fix':
                # ... which we don't want
                fixed.eof_blanks += len(buffer)
                buffer = []
        outf.write(''.join(buffer))

    for k in actions:
        assert fixed[k] in (seen[k],0)

    # count fixes for verbose output
    problems_seen = sum( seen[k] for k in actions )
    all_seen += problems_seen
    all_fixed += sum( fixed[k] for k in actions )
    if args.verbose>=1:
        if problems_seen>0 or args.verbose>=2:
            print>>stderr, "%s:" % fname
            if actions.trail_space:
                print>>stderr, "\t%s %d lines with trailing space" % ('CHOPPED' if actions.trail_space=='fix' else 'SAW', seen.trail_space)
            if actions.eof_blanks:
                print>>stderr, "\t%s %d blank lines at EOF" % ('CHOPPED' if actions.eof_blanks=='fix' else 'SAW', seen.eof_blanks)
            if actions.eof_newl:
                print>>stderr, "\t%s newline at EOF" % ('ADDED' if actions.eof_newl=='fix' and fixed.eof_newl else 'SAW MISSING' if seen.eof_newl else 'no change to')
            if actions.match_eol:
                print>>stderr, "\t%s %d line endings which didn't match %s from first line" % ('CHANGED' if actions.match_eol=='fix' else 'SAW', seen.match_eol, linesep)
            if coerce_eol:
                print>>stderr, "\tCOERCED %d line endings to %s" % (seen.coerce_eol, actions.coerce_eol)
            if actions.tab_space_mix:
                print>>stderr, "\tWARNED ABOUT %d lines with tabs/spaces mix" % seen.tab_space_mix

    if args.inplace:
        outf.close()

if not args.no_exit_codes:
    if all_seen>all_fixed:
        exit(20)
    elif all_seen:
        exit(10)
