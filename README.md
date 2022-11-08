# ia_whisperer

This tool uses OpenAI Whisper to create transcriptions for Internet Archive collections.

It manages creating a buffer of downloaded files to transcribe (so you're not waiting on downloads to finish to start your next transcription), and can handle multiple transcriptions in parallel (if you have a GPU that supports it, as Whisper doesn't fully use all cards).

(N.B. Whisper is not thread-safe, so ia_whisperer uses multiple processes. You mostly don't have to care about this, but don't panic if you read elsewhere that Whisper isn't thread-safe.)

## Prerequisites

* [Whisper](https://github.com/openai/whisper). Please see their instructions, but if you can run `whisper file.mp4`, you should be set.
* aria2c. Your OS will probably have a package for it.

## Usage

This probably needs to be cleaned up a fair bit. For now, you'll want to edit `ia_whisperer.py` a bit and make sure you're comfortable with some of the constants towards the top.

* `WHISPER_WORKERS`: If you have enough video RAM (12 GB *might* squeak by, less won't, more will), you may want to consider running with the default of two transcription processes, as it does a better job of using the video card. If your card hits 100% usage with one worker, there's no benefit to using more.

* `TEMP_DIR`: We download files to this directory before they are processed. Set it to something you don't mind having written and read a fair bit. Access speed is not super important: you can make this a spinning disk if you're normally on an SSD. Depending on the bitrate of things you're converting, you may want a spare 5-50 GB here.

* `TO_PROCESS_BUFFER_SECONDS`: We default to having a full day of videos to transcribe buffered, to smooth out any delays in downloading from the Internet Archive. This is generally fine, unless you're short on space. (If so, an hour or so may be better for you, but note that some items may be larger than that, meaning you're not building much of a buffer.)

* `ITEM_WORKERS`: How many files to download concurrently when we're downloading. This is probably fine at 2, since we have aria2 make multiple connections per file anyway.

Everything else should be fine as-is. (And in practice, you'll only need to fiddle with `WHISPER_WORKERS`, unless you have an RTX 3090, in which case you can just leave the defaults.)

Once you've got everything set up there, run `python3 ia_whisperer.py [collection name]` with some collection you want to generate subtitles for.

You can kill the process at any time, and it will cleanly abort. Re-running it will, after a bit of checking, pick back up where you left off (with the exception of any transcriptions that were going on when you killed it, which will be re-started from the beginning).

## Optimizations

* We load the Whisper model once at the beginning of each worker process, meaning less of a delay between files.
* We build a buffer of files to be processed so we don't have to wait for the next one to download when finishing a transcription.
* We run multiple transcriptions at once (if `WHISPER_WORKERS` is > 1).
* We can handle multiple video files in a single item.
* We download the smallest derived file for a video (if any), as some files are uploaded at very high quality (10+ gigabytes per hour).
* We download files from IA using multiple connections, which improves the speed (and we allow aria2 to reconnect as it wishes if a connection stalls out).
* When resuming, we skip items that have subtitles on the server, and skip any files that have local subtitles. We reuse any downloaded-but-unprocessed files as well.

## TODO?

* Make the script set `WHISPER_WORKERS` appropriately automatically.
* Consider deduplicating files if there are multiple with the same hash but different names in the same item? Unlikely to be a big savings.
* Only skip *files* that have corresponding subtitles, rather than skipping an entire item when it has any subtitles?
* Verify file hashes when downloading? (aria2 has an option for this)
