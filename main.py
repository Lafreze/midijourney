# encoding:utf-8
import requests
import os
import io, re
import json
import base64
import plugins
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common import const
from common.log import logger
from common.expired_dict import ExpiredDict
from plugins import *
from PIL import Image
from .mjapi import _mjApi
from .mjcache import _imgCache
from channel.chat_message import ChatMessage
from config import conf
import openai
from langdetect import detect
def check_prefix(content, prefix_list):
    if not prefix_list:
        return False, None
    for prefix in prefix_list:
        if content.startswith(prefix):
            return True, content.replace(prefix, "").strip()
    return False, None

def image_to_base64(image_path):
    filename, extension = os.path.splitext(image_path)
    t = extension[1:]
    with open(image_path, "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read())
        return f"data:image/{t};base64,{encoded_string.decode('utf-8')}"

def webp_to_png(webp_path):
    image_path = webp_path
    image_path = io.BytesIO()
    response = requests.get(webp_path)
    image = Image.open(io.BytesIO(response.content))
    image = image.convert("RGB")
    image.save(image_path, format="JPEG")
    return image_path

def read_file(path):
    with open(path, mode="r", encoding="utf-8") as f:
        return f.read()

def write_file(path, content):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(content, f, indent=4)
    return True

@plugins.register(
    name="MidJourney",
    namecn="MJ绘画",
    desc="一款AI绘画工具",
    version="1.0.29",
    author="mouxan",
    desire_priority=0
)
class MidJourney(Plugin):
    def __init__(self):
        super().__init__()

        gconf = {
            "mj_url": "",
            "mj_api_secret": "",
            "imagine_prefix": "[\"/i\", \"/mj\", \"/imagine\", \"/img\"]",
            "fetch_prefix": "[\"/f\", \"/fetch\"]",
            "up_prefix": "[\"/u\", \"/up\"]",
            "pad_prefix": "[\"/p\", \"/pad\"]",
            "blend_prefix": "[\"/b\", \"/blend\"]",
            "describe_prefix": "[\"/d\", \"/describe\"]"
        }

        # 读取和写入配置文件
        curdir = os.path.dirname(__file__)
        config_path = os.path.join(curdir, "config.json")
        if not os.path.exists(config_path):
            config_path = os.path.join(curdir, "config.json.template")
        if os.environ.get("mj_url", None):
            logger.info("使用的是环境变量配置")
            try:
                self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
            except Exception as e:
                if isinstance(e, FileNotFoundError):
                    logger.warn(f"[MJ] init failed, config.json not found.")
                else:
                    logger.warn("[MJ] init failed." + str(e))
                raise e
            gconf = {
                "mj_url": os.environ.get("mj_url", ""),
                "mj_api_secret": os.environ.get("mj_api_secret", ""),
                "openai_api_key": os.environ.get("openai_api_key", ""),
                "imagine_prefix": os.environ.get("imagine_prefix", "[\"/i\", \"/mj\", \"/imagine\", \"/img\"]"),
                "fetch_prefix": os.environ.get("fetch_prefix", "[\"/f\", \"/fetch\"]"),
                "up_prefix": os.environ.get("up_prefix", "[\"/u\", \"/up\"]"),
                "pad_prefix": os.environ.get("pad_prefix", "[\"/p\", \"/pad\"]"),
                "blend_prefix": os.environ.get("blend_prefix", "[\"/b\", \"/blend\"]"),
                "describe_prefix": os.environ.get("describe_prefix", "[\"/d\", \"/describe\"]")
            }
        else:
            logger.info(f"使用的是插件目录下的配置：{config_path}")
            gconf = {**gconf, **json.loads(read_file(config_path))}
        print(gconf)
        if gconf["mj_url"] == "":
            logger.info("[MJ] 未设置[mj_url]，请前往环境变量进行配置或在该插件目录下的config.json进行配置。")
        if not gconf["mj_url"] or not gconf["mj_api_secret"]:
            if not gconf["mj_url"]:
                gconf["mj_url"] = conf().get("mj_url", "") 
            if gconf["mj_url"] and not gconf["mj_api_secret"]:
                gconf["mj_api_secret"] = conf().get("mj_api_secret", "") 
            if not gconf["openai_api_key"]:
                gconf["openai_api_key"] = conf().get("open_ai_api_key", "")

        # 重新写入配置文件
        write_file(config_path, gconf)
        
        self.mj_url = gconf["mj_url"]
        self.mj_api_secret = gconf["mj_api_secret"]
        self.openai_api_key = gconf["openai_api_key"]

        if not gconf["imagine_prefix"]:
            self.imagine_prefix = ["/mj", "/imagine", "/img"]
        else:
            self.imagine_prefix = eval(gconf["imagine_prefix"])
        if not gconf["fetch_prefix"]:
            self.fetch_prefix = ["/ft", "/fetch"]
        else:
            self.fetch_prefix = eval(gconf["fetch_prefix"])
        if not gconf["up_prefix"]:
            self.up_prefix = ["/u", "/up"]
        else:
            self.up_prefix = eval(gconf["up_prefix"])
        if not gconf["pad_prefix"]:
            self.pad_prefix = ["/p", "/pad"]
        else:
            self.pad_prefix = eval(gconf["pad_prefix"])
        if not gconf["blend_prefix"]:
            self.blend_prefix = ["/b", "/blend"]
        else:
            self.blend_prefix = eval(gconf["blend_prefix"])
        if not gconf["describe_prefix"]:
            self.describe_prefix = ["/d", "/describe"]
        else:
            self.describe_prefix = eval(gconf["describe_prefix"])
        
        # 目前没有设计session过期事件，这里先暂时使用过期字典
        if conf().get("expires_in_seconds"):
            self.sessions = ExpiredDict(conf().get("expires_in_seconds"))
        else:
            self.sessions = dict()
        
        self.mj = _mjApi(self.mj_url, self.mj_api_secret, self.imagine_prefix, self.fetch_prefix, self.up_prefix, self.pad_prefix, self.blend_prefix, self.describe_prefix)

        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context

        logger.info("[MJ] inited. mj_url={} mj_api_secret={}".format(self.mj_url, self.mj_api_secret))
    def is_english(self, text):
        try:
            return detect(text) == 'en'
        except:
            return False
    def get_translation(self, iq):
        print(iq)
        last_match = ""
        api_key = self.openai_api_key
        model = "gpt-3.5-turbo-0613"
        if not self.is_english(iq) and iq is not None:
            try:
                print("input:",iq)
                if "--" in iq:
                    pattern_setting = iq.split("--")
                    for prompt in pattern_setting[1:]:
                        last_match += f"--{prompt}"
                    iq = pattern_setting[0]
                print("prompt",iq)
                message2 = f"""
                As a prompt generator for a generative AI called "Midjourney", you will create image prompts for the AI to visualize. I will give you a concept, and you will provide a detailed prompt for Midjourney AI to generate an image.

                Please adhere to the structure and formatting below, and follow these guidelines:

                - Do not use the words "description" or ":" in any form.
                - Do not place a comma between [ar] and [v].
                - Write each prompt in one line without using return.
                - Only provide the resulting text:.

                Structure:
                [1] = {iq}
                [2] = a detailed description of [1] with specific imagery details.
                [3] = a detailed description of the scene's environment.
                [4] = a detailed description of the scene's mood, feelings, and atmosphere.
                [5] = A style (e.g. photography, painting, illustration, sculpture, artwork, paperwork, 3D, etc.) for [1].
                [6] = A description of how [5] will be executed (e.g. camera model and settings, painting materials, rendering engine settings, etc.)
                [ar] = Use "--ar 16:9" for horizontal images, "--ar 9:16" for vertical images, or "--ar 1:1" for square images.
                [v] = Use "--niji" for Japanese art style, or "--v 5" for other styles.

                Formatting: 
                Follow this prompt structure: "[1], [2], [3], [4], [5], [6], [ar] [v]".

                Your task: Create only 1 prompt [1].

                - Write your prompt in English.
                - Do not describe unreal concepts as "real" or "photographic".
                - Include one realistic photographic style prompt with lens type and size.

                Example:
                A stunning Halo Reach landscape with a Spartan on a hilltop, lush green forests surround them, clear sky, distant city view, focusing on the Spartan's majestic pose, intricate armor, and weapons, Artwork, oil painting on canvas, --ar 16:9 --v 5
                """
                message1 = f"Translate the following text delimited by 「」 to English and only provide the resulting text: 「{iq}」"
                prompt = [{"role": "user", "content": message2}] if len(iq) < 10 else [{"role": "user", "content": message1}]
                response = openai.ChatCompletion.create(
                            api_key=api_key, model=model,
                                messages=prompt
                            )
                print("translated:",response["choices"][0]["message"]["content"])
                iq = response["choices"][0]["message"]["content"].replace("\"","").strip()
                if len(last_match) > 0:
                    iq = f"{iq} {last_match}"
                print("Final prompt:",iq)
                print('#tokens:',response['usage']['total_tokens'])
            except Exception as e:
                print(e)
        return iq
    def on_handle_context(self, e_context: EventContext):
        if e_context["context"].type not in [
            ContextType.TEXT,
            ContextType.IMAGE,
        ]:
            return

        channel = e_context['channel']
        context = e_context['context']
        content = context.content
        msg: ChatMessage = context["msg"]
        sessionid = context["session_id"]
        isgroup = e_context["context"].get("isgroup", False)
        reply = None

        # 图片
        if ContextType.IMAGE == context.type:
            # 需要调用准备函数下载图片，否则会出错
            msg.prepare()
            base64 = image_to_base64(content)
            img_cache = None
            if sessionid in self.sessions:
                img_cache = self.sessions[sessionid].get_cache()
            
            # 识别图片
            # if (not isgroup and not img_cache) or (not isgroup and not img_cache["instruct"]) or (img_cache and img_cache["instruct"] == "describe"):
            if img_cache and img_cache["instruct"] == "describe":
                self.env_detection(e_context)
                reply = self.describe(base64, channel, context)
                if sessionid in self.sessions:
                    self.sessions[sessionid].reset()
                    del self.sessions[sessionid]
            
            if img_cache and img_cache["instruct"] == "imagine":
                self.env_detection(e_context)
                prompt = img_cache["prompt"]
                reply = self.imagine(prompt, base64, channel, context)
                if sessionid in self.sessions:
                    self.sessions[sessionid].reset()
                    del self.sessions[sessionid]
            
            if img_cache and img_cache["instruct"] == "blend":
                self.env_detection(e_context)
                self.sessions[sessionid].action(base64)
                img_cache = self.sessions[sessionid].get_cache()
                length = len(img_cache["base64Array"])
                if length < 2:
                    reply = Reply(ReplyType.TEXT, f"✏  请再发送一张或多张图片")
                else:
                    reply = Reply(ReplyType.TEXT, f"✏  您已发送{length}张图片，可以发送更多图片或者发送[/end]开始合成")
            
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            return


        if ContextType.TEXT == context.type:
            # 判断是否是指令
            iprefix, iq = check_prefix(content, self.imagine_prefix)
            fprefix, fq = check_prefix(content, self.fetch_prefix)
            uprefix, uq = check_prefix(content, self.up_prefix)
            pprefix, pq = check_prefix(content, self.pad_prefix)
            bprefix, bq = check_prefix(content, self.blend_prefix)
            dprefix, dq = check_prefix(content, self.describe_prefix)
            
            if content == "/mjhp" or content == "/mjhelp" or content == "/mj-help":
                reply = Reply(ReplyType.INFO, self.mj.help_text())
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS  # 事件结束，并跳过处理context的默认逻辑
                return
            elif iprefix == True:
                self.env_detection(e_context)
                iq = self.get_translation(iq)
                reply = self.imagine(iq, "", channel, context)
                if sessionid in self.sessions:
                    self.sessions[sessionid].reset()
                    del self.sessions[sessionid]
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return
            elif uprefix == True:
                self.env_detection(e_context)
                reply = self.up(uq, channel, context)
                if sessionid in self.sessions:
                    self.sessions[sessionid].reset()
                    del self.sessions[sessionid]
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return
            elif pprefix == True:
                self.env_detection(e_context)
                if not pq:
                    reply = Reply(ReplyType.TEXT, "✨ 垫图模式\n✏ 请在指令后输入要绘制的描述文字")
                else:
                    pq = self.get_translation(pq)
                    self.sessions[sessionid] = _imgCache(sessionid, "imagine", pq)
                    reply = Reply(ReplyType.TEXT, "✨ 垫图模式\n✏ 请再发送一张图片")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return
            elif bprefix == True:
                self.env_detection(e_context)
                self.sessions[sessionid] = _imgCache(sessionid, "blend", bq)
                reply = Reply(ReplyType.TEXT, "✨ 混图模式\n✏ 请发送两张或多张图片，然后输入['/end']结束")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return
            elif dprefix == True:
                self.env_detection(e_context)
                self.sessions[sessionid] = _imgCache(sessionid, "describe", dq)
                reply = Reply(ReplyType.TEXT, "✨ 识图模式\n✏ 请发送一张图片")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return
            elif content.startswith("/re"):
                self.env_detection(e_context)
                id = content.replace("/re", "").strip()
                reply = self.reroll(id, channel, context)
                if sessionid in self.sessions:
                    self.sessions[sessionid].reset()
                    del self.sessions[sessionid]
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return
            elif content == "/end":
                self.env_detection(e_context)
                # 从会话中获取缓存的图片
                img_cache = None
                if sessionid in self.sessions:
                    img_cache = self.sessions[sessionid].get_cache()
                if not img_cache:
                    reply = Reply(ReplyType.TEXT, "请先输入指令开启绘图模式")
                    e_context["reply"] = reply
                    e_context.action = EventAction.BREAK_PASS
                    return
                base64Array = img_cache["base64Array"]
                prompt = img_cache["prompt"]
                length = len(base64Array)
                if length >= 2:
                    reply = self.blend(img_cache["base64Array"], prompt, channel, context)
                    if sessionid in self.sessions:
                        self.sessions[sessionid].reset()
                        del self.sessions[sessionid]
                elif length == 0:
                    reply = Reply(ReplyType.TEXT, "✨ 混图模式\n✏ 请发送两张或多张图片方可完成混图")
                else:
                    reply = Reply(ReplyType.TEXT, "✨ 混图模式\n✏ 请再发送一张图片方可完成混图")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return
            elif fprefix == True:
                self.env_detection(e_context)
                logger.debug("[MJ] /fetch id={}".format(fq))
                status, msg, imageUrl = self.mj.fetch(fq)
                if status:
                    if imageUrl:
                        self.sendMsg(msg, channel, context)
                        image_path = webp_to_png(imageUrl)
                        reply = Reply(ReplyType.IMAGE, image_path)
                    else:
                        reply = Reply(ReplyType.TEXT, msg)
                else:
                    reply = Reply(ReplyType.ERROR, msg)
                if sessionid in self.sessions:
                    self.sessions[sessionid].reset()
                    del self.sessions[sessionid]
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return
            elif content == "/queue":
                self.env_detection(e_context)
                status, msg = self.mj.task_queue()
                if status:
                    reply = Reply(ReplyType.TEXT, msg)
                else:
                    reply = Reply(ReplyType.ERROR, msg)
                if sessionid in self.sessions:
                    self.sessions[sessionid].reset()
                    del self.sessions[sessionid]
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return

    def get_help_text(self, **kwargs):
        if kwargs.get("verbose") != True:
            return "这是一个AI绘画工具，只要输入想到的文字，通过人工智能产出相对应的图。"
        else:
            return self.mj.help_text()
    
    def env_detection(self, e_context: EventContext):
        if not self.mj_url:
            reply = Reply(ReplyType.ERROR, "未设置[mj_url]，请前往环境变量进行配置或在该插件目录下的config.json进行配置。")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            return
    
    def imagine(self, prompt, base64, channel, context):
        logger.debug("[MJ] /imagine prompt={} img={}".format(prompt, base64))
        reply = None
        status, msg, id = self.mj.imagine(prompt, base64)
        if status:
            self.sendMsg(msg, channel, context)
            reply = self.get_f_img(id, channel, context)
        else:
            reply = Reply(ReplyType.ERROR, msg)
        return reply
    
    def up(self, id, channel, context):
        logger.debug("[MJ] /up id={}".format(id))
        reply = None
        status, msg, id = self.mj.simpleChange(id)
        if status:
            self.sendMsg(msg, channel, context)
            reply = self.get_f_img(id, channel, context)
        else:
            reply = Reply(ReplyType.ERROR, msg)
        return reply
    
    def describe(self, base64, channel, context):
        logger.debug("[MJ] /describe img={}".format(base64))
        reply = None
        status, msg, id = self.mj.describe(base64)
        if status:
            self.sendMsg(msg, channel, context)
            reply = self.get_f_img(id, channel, context)
        else:
            reply = Reply(ReplyType.ERROR, msg)
        return reply
    
    def blend(self, base64Array, dimensions, channel, context):
        logger.debug("[MJ] /blend imgList={} dimensions={}".format(base64Array, dimensions))
        reply = None
        status, msg, id = self.mj.blend(base64Array, dimensions)
        if status:
            self.sendMsg(msg, channel, context)
            reply = self.get_f_img(id, channel, context)
        else:
            reply = Reply(ReplyType.ERROR, msg)
        return reply
    
    def reroll(self, id, channel, context):
        logger.debug("[MJ] /reroll id={}".format(id))
        reply = None
        status, msg, id = self.mj.reroll(id)
        if status:
            self.sendMsg(msg, channel, context)
            reply = self.get_f_img(id, channel, context)
        else:
            reply = Reply(ReplyType.ERROR, msg)
        return reply
    
    def get_f_img(self, id, channel, context):
        input_content = context.content
        status2, msg, imageUrl = self.mj.get_f_img(id,input_content)
        
        if status2:
            if imageUrl:
                self.sendMsg(msg, channel, context)
                image_path = webp_to_png(imageUrl)
                reply = Reply(ReplyType.IMAGE, image_path)
            else:
                reply = Reply(ReplyType.TEXT, msg)
        else:
            reply = Reply(ReplyType.ERROR, msg)
        return reply
    
    def sendMsg(self, msg, channel, context, types=ReplyType.TEXT):
        return channel._send_reply(context, channel._decorate_reply(context, Reply(types, msg)))
