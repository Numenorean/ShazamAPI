# Shazam Api

### Install
```
pip3 install ShazamAPI
```
Also you need to install ffmpeg and ffprobe than add it to path

### Usage
```python
from ShazamAPI import Shazam

mp3_file_content_to_recognize = open('a.mp3', 'rb').read()

shazam = Shazam(mp3_file_content_to_recognize)
recognize_generator = shazam.recognizeSong()
while True:
	print(next(recognize_generator)) # current offset & shazam response to recognize requests
```

### Credits to:
https://github.com/marin-m/SongRec
