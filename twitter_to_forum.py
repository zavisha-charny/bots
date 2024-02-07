import os
import time
import logging
from datetime import datetime

import httpx
import tweepy
import pandas as pd
from dotenv import load_dotenv


logger = logging.getLogger()
load_dotenv()


IDS_FILE = './extant_tweet_ids.csv'
TWITTER_BOT_USERNAME = '@mybot'
CID = -1  # create new category for Twitter re-posts
BASE_URL = 'https://bioenergetics.forum/api/v3/'
HEADERS = {'Content-Type': 'application/json'}
CREDENTIALS = {
  'username': os.environ['FORUM_USERNAME'],
  'password': os.environ['FORUM_PASSWORD'],
}


class Tweet:
    DATE_FORMAT = '%Y-%m-%d %H:%M'

    def __init__(self, status_dict: dict) -> None:
        self.id = status_dict['id']
        self.user = status_dict['user']['screen_name']
        self.user_tag = status_dict['user']['name']
        self.text = status_dict.get('full_text', status_dict.get('text'))
        if self.text is None:
            raise ValueError(f'text can not be None: {status_dict = }')
        self.created_at = datetime.strptime(status_dict['created_at'], '%a %b %d %H:%M:%S %z %Y')
        self.in_reply_to_status_id = status_dict['in_reply_to_status_id']
        self.in_reply_to_user_id = status_dict['in_reply_to_user_id']
        self.in_reply_to_screen_name = status_dict['in_reply_to_screen_name']
        self.is_quote = status_dict['is_quote_status']

        self.media = status_dict['entities'].get('media')

    def __str__(self) -> str:
        header = f'[{self.created_at.strftime(Tweet.DATE_FORMAT)}] (@{self.user_tag}) {self.user}'
        if self.in_reply_to_screen_name is not None: 
            header += f' to {self.in_reply_to_screen_name}'
        if self.media:
            img_urls = [m['media_url_https'] for m in self.media]
            footer = '\n' + ' '.join([f'[{os.path.basename(url)}]({url})' for url in img_urls])
        else:
            footer = ''
        return f'{header}:\n{self.text}{footer}'


def post_forum(posts: list[dict]) -> bool:
    posts = [Tweet(post) for post in posts]
    with httpx.Client() as client:
        # create session
        client.post(
            BASE_URL+'utilities/login', 
            headers=HEADERS,
            json=CREDENTIALS
        )
        # create a new topic
        first_post = str(posts[0])
        title = first_post[:70]
        if len(first_post) < 70:
            title += '...'
        create_payload = {
            'cid': CID,
            'title': title,
            'content': first_post,
            'timestamp': time.time_ns(),
            'tags': [],  # some NLP to extract keywords?
        }
        response = client.post(
            BASE_URL+'topics/',
            headers=HEADERS,
            data=create_payload,
        )
        response.raise_for_status()  # how to handle errors?
        tid = response.json()['response']['tid']
        # post all other tweets
        for post in posts[1:]:
            post = str(post)
            post_payload = {
                'content': post,
                'toPid': 0,  # ???
            }
            response = client.post(
                BASE_URL+f'topics/{tid}',
                headers=HEADERS,
                data=post_payload,
            )
            response.raise_for_status()



def unroll_thread(api: tweepy.API, comment_status: dict) -> tuple[str, str, list]:
    """
    Scraps tweets from the start of the thread to the tagging comment. 
    Returns the thread display name, username, and the list of tweets.
    """
    thread_id = comment_status.in_reply_to_status_id
    start_of_thread = api.get_status(thread_id)
    author_username = start_of_thread.username

    replies = []
    for reply in tweepy.Cursor(
        api.search_tweets, 
        q=f'to:@{author_username}', 
        tweet_mode='extended',
    ).items():
        if hasattr(reply, 'in_reply_to_status_id'):
            if (reply.in_reply_to_status_id == thread_id):
                replies.append(reply)
    replies = replies[::-1]

    return replies


class MyStreamListener(tweepy.StreamingClient):

    def on_tweet(self, tweet: dict) -> None:
        if not self.__is_valid_comment(tweet):
            return
        author_display, author_username, tweets = unroll_thread(tweet.id_str)
        post_forum(author_display, author_username, tweets)
        self.__save_tweet_id(tweet['id'])
        self.__on_success(tweet)

    def __on_success(self, tweet: dict) -> None:
        # save to csv
        with open(IDS_FILE, 'a') as f:
            f.write(str(tweet['id'])+',\n')
        # respond
        self.__respond_on_success(tweet)

    def __respond_on_success(self, tweet: dict) -> None:
        pass

    def __is_valid_comment(self, tweet: dict) -> bool:
        id_ = tweet['id']
        df = pd.read_csv(IDS_FILE, names=['id'])
        return df.loc[df['id'] == id_].empty

    def __save_tweet_id(self, tweet_id: int) -> None:
        with open(IDS_FILE, 'a') as f:
            f.write(f'{tweet_id},\n')



def main() -> None:
    if not os.path.exists(IDS_FILE):
        with open(IDS_FILE, 'w') as _:
            pass

    CONSUMER_KEY = os.environ['API_KEY']
    CONSUMER_SECRET = os.environ['API_KEY_SECRET']
    BEARER_TOKEN = os.environ['BEARER_TOKEN']
    ACCESS_TOKEN = os.environ['ACCESS_TOKEN']
    ACCESS_TOKEN_SECRET = os.environ['ACCESS_TOKEN_SECRET']

    # authenticate to Twitter
    auth = tweepy.OAuthHandler(CONSUMER_KEY, CONSUMER_SECRET)
    auth.set_access_token(ACCESS_TOKEN, ACCESS_TOKEN_SECRET)

    # create API object
    api = tweepy.API(auth, wait_on_rate_limit=True)

    # create a stream listener
    api = object
    my_stream_listener = MyStreamListener()
    my_stream = tweepy.Stream(auth=api.auth, listener=my_stream_listener)

    # start the stream to listen for mentions
    my_stream.filter(track=[TWITTER_BOT_USERNAME])

