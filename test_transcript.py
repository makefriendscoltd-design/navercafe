import sys
from youtube_transcript_api import YouTubeTranscriptApi

def extract_transcript_for_manual_check(video_id):
    print(f"Testing extraction for video ID: {video_id}")
    try:
        transcript = YouTubeTranscriptApi().fetch(video_id, languages=['ko', 'en'])
        text = " ".join([t.text for t in transcript])
        print("Success! Extracted text preview:")
        print(text[:200] + "...")
    except Exception as e:
        print(f"Failed extraction: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        extract_transcript_for_manual_check(sys.argv[1])
    else:
        extract_transcript_for_manual_check("jNQXAC9IVRw") # Me at the zoo
