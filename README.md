# Shazam Api

### Usage
```python
import ShazamAPI

mp3_file_content_to_recognize = open('a.mp3', 'rb').read()

shazam = ShazamAPI(mp3_file_content_to_recognize)
recognize_generator = shazam.recognizeSong()
while True:
	print(next(recognize_generator)) # current offset & shazam response to recognize requests
```

### Credits to:
https://github.com/marin-m/SongRec