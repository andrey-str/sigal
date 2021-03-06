# -*- coding:utf-8 -*-

# Copyright (c) 2009-2013 - Simon Conseil

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to
# deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
# sell copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
# IN THE SOFTWARE.

from __future__ import absolute_import

import codecs
import logging
import markdown
import os
import sys

from clint.textui import progress, colored
from multiprocessing import Pool
from os.path import join
from PIL import Image as PILImage

from .image import Image
from .settings import get_thumb
from .writer import Writer

DESCRIPTION_FILE = "index.md"


class PathsDb(object):
    """Container for all the information on the directory structure.

    All the info is stored in a dictionnary, `self.db`. This class also has
    methods to build this dictionnary.

    """

    def __init__(self, path, ext_list):
        self.basepath = path
        self.ext_list = ext_list
        self.logger = logging.getLogger(__name__)

    def get_subdirs(self, path):
        """Return the list of all sub-directories of path."""

        subdir = [os.path.normpath(os.path.join(path, sub))
                  for sub in self.db[path].get('subdir', [])]
        if subdir:
            return subdir + reduce(lambda x, y: x+y, map(self.get_subdirs, subdir))
        else:
            return []

    def build(self):
        "Build the list of directories with images"

        # The dict containing all information
        self.db = {
            'paths_list': [],
            'skipped_dir': []
        }

        # get information for each directory
        for path, dirnames, filenames in os.walk(self.basepath):
            relpath = os.path.relpath(path, self.basepath)

            # sort images and sub-albums by name
            filenames.sort(key=str.lower)
            dirnames.sort(key=str.lower)

            self.db['paths_list'].append(relpath)
            self.db[relpath] = {
                'img': [f for f in filenames
                        if os.path.splitext(f)[1] in self.ext_list],
                'subdir': dirnames
            }
            self.db[relpath].update(get_metadata(path))

        path_im = [path for path in self.db['paths_list']
                   if self.db[path]['img'] and path != '.']
        path_noim = [path for path in self.db['paths_list']
                     if not self.db[path]['img'] and path != '.']

        # dir with images: check the thumbnail, and find it if necessary
        for path in path_im:
            self.check_thumbnail(path)

        # dir without images, start with the deepest ones
        for path in reversed(sorted(path_noim, key=lambda x: x.count('/'))):
            for subdir in self.get_subdirs(path):
                # use the thumbnail of their sub-directories
                if self.db[subdir].get('thumbnail', ''):
                    self.db[path]['thumbnail'] = join(
                        os.path.relpath(subdir, path),
                        self.db[subdir]['thumbnail'])
                    break

            if not self.db[path].get('thumbnail', ''):
                # else remove all info about this directory
                self.logger.info("Directory '%s' is empty", path)
                self.db['skipped_dir'].append(path)
                self.db['paths_list'].remove(path)
                del self.db[path]
                parent = os.path.normpath(join(path, '..'))
                child = os.path.relpath(path, parent)
                self.db[parent]['subdir'].remove(child)

    def check_thumbnail(self, path):
        "Find the thumbnail image for a given path."

        # stop if it is already set and a valid file
        alb_thumb = self.db[path].setdefault('thumbnail', '')
        if alb_thumb and os.path.isfile(join(self.basepath, path, alb_thumb)):
            return

        # find and return the first landscape image
        for f in self.db[path]['img']:
            im = PILImage.open(join(self.basepath, path, f))
            if im.size[0] > im.size[1]:
                self.db[path]['thumbnail'] = f
                return

        # else simply return the 1st image
        if self.db[path]['img']:
            self.db[path]['thumbnail'] = self.db[path]['img'][0]
        else:
            self.db[path]['thumbnail'] = ''


class Gallery(object):
    "Prepare images"

    def __init__(self, settings, input_dir, output_dir, force=False,
                 theme=None, ncpu=1):
        self.settings = settings
        self.force = force
        self.ncpu = ncpu
        self.input_dir = os.path.abspath(input_dir)
        self.output_dir = os.path.abspath(output_dir)
        self.logger = logging.getLogger(__name__)
        self.writer = Writer(settings, output_dir, theme=theme)

        self.paths = PathsDb(self.input_dir, self.settings['ext_list'])
        self.paths.build()
        self.db = self.paths.db

    def build(self):
        "Create the image gallery"

        self.logger.info("Generate gallery in %s ...", self.output_dir)
        check_or_create_dir(self.output_dir)

        # Compute the label with for the progress bar. The max value is 48
        # character = 80 - 32 for the progress bar.
        label_width = max((len(p) for p in self.db['paths_list'])) + 1
        label_width = min(label_width, 48)

        # loop on directories in reversed order, to process subdirectories
        # before their parent
        for path in reversed(self.db['paths_list']):
            imglist = [join(self.input_dir, path, f)
                       for f in self.db[path]['img']]

            # output dir for the current path
            img_out = join(self.output_dir, path)
            check_or_create_dir(img_out)

            if len(imglist) != 0:
                self.process_dir(imglist, img_out, path,
                                 label_width=label_width)

            self.writer.write(self.db, path)

    def process_dir(self, imglist, outpath, dirname, label_width=20):
        """Process a list of images in a directory."""

        # Create thumbnails directory and optionally the one for original img
        check_or_create_dir(join(outpath, self.settings['thumb_dir']))

        if self.settings['keep_orig']:
            check_or_create_dir(join(outpath, self.settings['orig_dir']))

        # use progressbar if level is > INFO
        if self.logger.getEffectiveLevel() > 20:
            label = colored.green(dirname.ljust(label_width))
            img_iterator = progress.bar(imglist, label=label)
        else:
            img_iterator = iter(imglist)
            self.logger.info(":: Processing '%s' [%i images]",
                             colored.green(dirname), len(imglist))

        pool = Pool(processes=self.ncpu)

        try:
            # loop on images
            for f in img_iterator:
                filename = os.path.split(f)[1]
                outname = join(outpath, filename)
                self.logger.info(filename)

                if os.path.isfile(outname) and not self.force:
                    self.logger.info("%s exists - skipping", filename)
                else:
                    self.logger.info(filename)
                    pool.apply_async(worker_image,
                                     (f, outpath, self.settings)).get(9999)

            pool.close()
            pool.join()
        except KeyboardInterrupt:
            pool.terminate()
            sys.exit('Interrupted')


def worker_image(*args):
    try:
        return process_image(*args)
    except KeyboardInterrupt:
        return 'KeyboardException'


def process_image(filepath, outpath, settings):
    """Process one image: resize, create thumbnail."""

    filename = os.path.split(filepath)[1]
    outname = join(outpath, filename)

    img = Image(filepath)

    if settings['keep_orig']:
        img.save(join(outpath, settings['orig_dir'], filename),
                 **settings['jpg_options'])

    img.resize(settings['img_size'])

    if settings['copyright']:
        img.add_copyright(settings['copyright'])

    img.save(outname, **settings['jpg_options'])

    if settings['make_thumbs']:
        thumb_name = join(outpath, get_thumb(settings, filename))
        img.thumbnail(thumb_name, settings['thumb_size'],
                      fit=settings['thumb_fit'],
                      quality=settings['jpg_options']['quality'])


def get_metadata(path):
    """ Get album metadata from DESCRIPTION_FILE:

    - title
    - thumbnail image
    - description
    """

    descfile = join(path, DESCRIPTION_FILE)

    if not os.path.isfile(descfile):
        # default: get title from directory name
        meta = {
            'title': os.path.basename(path).replace('_', ' ')
            .replace('-', ' ').capitalize(),
            'description': '',
            'thumbnail': ''
        }
    else:
        md = markdown.Markdown(extensions=['meta'])

        with codecs.open(descfile, "r", "utf-8") as f:
            text = f.read()

        html = md.convert(text)

        meta = {
            'title': md.Meta.get('title', [''])[0],
            'description': html,
            'thumbnail': md.Meta.get('thumbnail', [''])[0]
        }

    return meta


def check_or_create_dir(path):
    "Create the directory if it does not exist"

    if not os.path.isdir(path):
        os.makedirs(path)
