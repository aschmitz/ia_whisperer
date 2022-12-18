from math import floor
from multiprocessing import Process, Manager
from os import makedirs, path, unlink
import re
import subprocess
import sys
import time
import traceback

import internetarchive
import ffmpeg
import numpy
import whisper

if len(sys.argv) < 2:
    print(f"Usage: {sys.argv[0]} [collection name]")
    exit(1)


TEMP_DIR = 'download'
COLLECTION_NAME = sys.argv[1]
ITEM_WORKERS = 2
WHISPER_WORKERS = 2
TO_PROCESS_BUFFER_SECONDS = 60 * 60 * 24 # build a 24-hour buffer of video

WHISPER_MODEL = 'medium'
WHISPER_LANGUAGE = 'English'

# We don't care a lot about fragmentation here, we'll be deleting the files
# shortly anyway.
ARIA2_FILE_ALLOCATION = 'none'

# Mirrors default values in OpenAI's Whisper:
WHISPER_DEVICE = 'cuda' # or 'cpu' if you have no CUDA devices
WHISPER_TEMPERATURE = tuple(numpy.arange(0, 1.0 + 1e-6, 0.2))

FILE_REGEX = re.compile(r'^(?P<base>.+?)(?:\.ia|_(?:512|256|128|64)kb)?\.(?P<ext>mp4|avi|mov|ogv|mpeg|flac|mp3|wav)$')
SUBTITLE_REGEX = re.compile(r'\.(stt|vtt)$')


def collection_processor(item_queue):
    # Get items by their publication date, oldest to newest.
    search = internetarchive.search_items(
        query=f'collection:({COLLECTION_NAME})', sorts=['publicdate'])
    for item in search.iter_as_items():
        item_queue.put(item.identifier)

    for _ in range(0, ITEM_WORKERS):
        item_queue.put('DONE')

    print('Collection worker done')


def item_processor(downloaded_length, item_queue, whisper_queue):
    while True:
        while downloaded_length.value > TO_PROCESS_BUFFER_SECONDS:
            time.sleep(1)

        item_name = item_queue.get()
        if item_name == 'DONE':
            break

        item = internetarchive.get_item(item_name)
        files = item_files(item)

        for details in files:
            if path.exists(file_subtitle_path(details, 'vtt')):
                print(f"We have local subtitles for {item.identifier}/{details['base']}")
                continue

            ia_file = item.get_file(details['name'], details)

            try:
                if (not path.exists(file_download_path(details))) or \
                    path.exists(f"{file_download_path(details)}.aria2"):
                    subprocess.run([
                        'aria2c',
                        f'--file-allocation={ARIA2_FILE_ALLOCATION}',
                        '--max-concurrent-downloads=10',
                        '--max-connection-per-server=10',
                        # '--download-result=hide',
                        '--console-log-level=warn',
                        f'--out={file_download_path(details)}',
                        ia_file.url])

                try:
                    downloaded_length.value += floor(float(details['length']))
                except:
                    # For some reason we couldn't parse the length. Assume it
                    # was about five minutes - this will likely be wrong, but
                    # it's good enough.
                    downloaded_length.value += 300

                whisper_queue.put(details)

                print(f"Downloaded {item.identifier}/{details['base']}, {downloaded_length.value} seconds of video buffered")
            except Exception:
                print(f"Exception while downloading {item.identifier}/{details['base']}:")
                traceback.print_exc()

    print('Item worker done')

def file_processor(downloaded_length, whisper_queue):
    model = whisper.load_model(WHISPER_MODEL, device=WHISPER_DEVICE)

    while True:
        details = whisper_queue.get()
        if details == 'DONE':
            break

        try:
            transcribed = whisper.transcribe(
                model, file_download_path(details), temperature=WHISPER_TEMPERATURE,
                language=WHISPER_LANGUAGE, verbose=True)
        except Exception as e:
            print("FFmpeg error, skipping this file")
            downloaded_length.value -= floor(float(details['length']))
            continue

        ensure_file_item_path(details)

        with open(file_subtitle_path(details, 'txt'), 'w', encoding='utf-8') as txt:
            whisper.utils.write_txt(transcribed['segments'], file=txt)

        with open(file_subtitle_path(details, 'vtt'), 'w', encoding='utf-8') as vtt:
            whisper.utils.write_vtt(transcribed['segments'], file=vtt)

        downloaded_length.value -= floor(float(details['length']))

        try:
            unlink(file_download_path(details))
        except Exception as e:
            # Do nothing: this shouldn't happen much, but *could* happen if for
            # some reason we process a file with the same hash multiple times.
            # We don't do that on our own, but sometimes the same file gets
            # uploaded twice to an item, etc. (It would be nice to catch this
            # earlier and handle it somewhere else, but it's not exceptionally
            # common.)
            pass

        print(f"Processed {details['item_id']}/{details['base']}, {downloaded_length.value} seconds of video buffered")

    print('File worker done')

def item_files(item):
    bases = {}

    for file in item.files:
        if SUBTITLE_REGEX.search(file['name']):
            print(f"Item {item.identifier} already has subtitles")
            return []
        elif file_parts := FILE_REGEX.search(file['name']):
            file_parts = file_parts.groupdict()
            if not file_parts['base'] in bases or \
                file['size'] < bases[file_parts['base']]['size']:
                file['item_id'] = item.identifier
                file['base'] = file_parts['base']
                file['extension'] = file_parts['ext']
                bases[file_parts['base']] = file

    return bases.values()


def file_download_path(details):
    return path.join(TEMP_DIR, f"{details['sha1']}.{details['extension']}")


def ensure_file_item_path(details):
    item_path = path.dirname(file_subtitle_path(details, 'txt'))
    if not path.exists(item_path):
        makedirs(item_path, exist_ok=True)


def file_subtitle_path(details, ext):
    return path.join(COLLECTION_NAME, details['item_id'], f"{details['base']}.autogenerated.{ext}")


if __name__ == '__main__':
    with Manager() as manager:
        downloaded_length = manager.Value('i', 0)
        item_queue = manager.Queue()
        whisper_queue = manager.Queue()

        collection_process = Process(
            target=collection_processor,
            args=(item_queue,))
        collection_process.start()

        item_workers = list()
        for _ in range(0, ITEM_WORKERS):
            item_worker = Process(
                target=item_processor,
                args=(downloaded_length, item_queue, whisper_queue))
            item_worker.start()
            item_workers.append(item_worker)

        whisper_workers = list()
        for _ in range(0, WHISPER_WORKERS):
            whisper_worker = Process(
                target=file_processor,
                args=(downloaded_length, whisper_queue))
            whisper_worker.start()
            whisper_workers.append(whisper_worker)

        collection_process.join()
        for worker in item_workers:
            worker.join()

        # Once we've processed all the items, whisper will be done too.
        for _ in range(0, WHISPER_WORKERS):
            whisper_queue.put('DONE')

        for worker in whisper_workers:
            worker.join()

        print("We're all done here!")
