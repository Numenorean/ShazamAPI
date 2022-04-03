# Shazam Api

### Install
```
pip3 install ShazamAPI
```
Also you need to install ffmpeg and ffprobe then add it to path

### Usage

```python
from ShazamAPI import Shazam

with open('filename.mp3', 'rb') as fp:
    mp3_file_content_to_recognize = fp.read()

recognize_generator = Shazam().recognize_song(mp3_file_content_to_recognize)
# or just:
#     recognize_generator = Shazam().recognize_song('filename.mp3')
for (offset, resp) in recognize_generator:
    print(offset, resp)
```

### Credits to:

https://github.com/marin-m/SongRec
