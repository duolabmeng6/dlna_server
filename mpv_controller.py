import sys
import threading
import time
import json
import os
import platform
import subprocess
import socket
import tempfile
import random
from PySide6.QtCore import Signal, QObject, QTimer, QMetaObject, Qt, Slot
import shlex

# 添加获取基础路径的函数
def get_base_path(path="."):
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.getcwd()
    return os.path.join(base_path, path)

# 设置MPV默认路径
def set_mpv_default_path():
    mpv_path = 'mpv'
    if sys.platform == 'darwin':
        # 先尝试从应用程序包内获取mpv路径
        bundled_mpv = get_base_path('bin/MacOS/mpv')
        if os.path.exists(bundled_mpv):
            mpv_path = bundled_mpv
        # 如果不存在，尝试从常见位置获取
        elif os.path.exists('/usr/local/bin/mpv'):
            mpv_path = '/usr/local/bin/mpv'
        elif os.path.exists('/opt/homebrew/bin/mpv'):
            mpv_path = '/opt/homebrew/bin/mpv'
    elif sys.platform == 'win32':
        bundled_mpv = get_base_path('bin/mpv.exe')
        if os.path.exists(bundled_mpv):
            mpv_path = bundled_mpv
    
    return mpv_path

# 查找IINA路径
def find_iina_path():
    iina_path = None
    if platform.system() == "Darwin":
        # 在macOS上，先检查应用程序目录
        paths_to_check = [
            "/Applications/IINA.app/Contents/MacOS/IINA",
            os.path.expanduser("~/Applications/IINA.app/Contents/MacOS/IINA")
        ]
        for path in paths_to_check:
            if os.path.exists(path):
                iina_path = path
                break
    return iina_path

# IINA控制器类，用于控制IINA播放器
class IINAController(QObject):
    # 信号定义
    iina_state_changed = Signal(str)  # 播放状态变化
    iina_connection_error = Signal(str)  # 连接错误
    iina_position_changed = Signal(int)  # 播放位置变化（秒）
    iina_duration_changed = Signal(int)  # 总时长变化（秒）
    iina_volume_changed = Signal(int)  # 音量变化
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None
        self.dlna_server = None  # DLNA服务器引用
        self.iina_cli_path = None
        self.socket_path = "/tmp/iina-socket"
        self.running = False
        self.receive_thread = None
        self.command_counter = 1
        
        # 查找iina-cli路径
        self._find_iina_cli()
    
    def _find_iina_cli(self):
        """查找iina-cli路径"""
        iina_cli_paths = [
            "/Applications/IINA.app/Contents/MacOS/iina-cli",
            os.path.expanduser("~/Applications/IINA.app/Contents/MacOS/iina-cli")
        ]
        
        for path in iina_cli_paths:
            if os.path.exists(path):
                self.iina_cli_path = path
                print(f"找到IINA命令行工具: {path}")
                break
    
    def _send_ipc_command(self, command):
        """发送IPC命令到IINA"""
        try:
            if os.path.exists(self.socket_path):
                command_json = json.dumps(command) + "\n"
                # 使用socat命令发送IPC命令
                cmd = f"echo '{command_json}' | socat - {self.socket_path}"
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                if result.returncode == 0:
                    return True
                else:
                    print(f"IPC命令发送失败: {result.stderr}")
                    return False
            else:
                print(f"IPC套接字不存在: {self.socket_path}")
                return False
        except Exception as e:
            print(f"发送IPC命令错误: {e}")
            return False
    
    def _send_ipc_command_with_response(self, command):
        """发送IPC命令到IINA并获取响应"""
        try:
            if os.path.exists(self.socket_path):
                # 添加请求ID
                if 'request_id' not in command:
                    command['request_id'] = self.command_counter
                    self.command_counter += 1
                
                command_json = json.dumps(command) + "\n"
                # 使用socat命令发送IPC命令并获取响应
                cmd = ["socat", "-", self.socket_path]
                process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                stdout, stderr = process.communicate(input=command_json, timeout=2.0)
                
                if process.returncode == 0 and stdout:
                    try:
                        return json.loads(stdout)
                    except json.JSONDecodeError:
                        print(f"无法解析IINA响应: {stdout}")
                        return None
                else:
                    print(f"IPC命令执行失败: {stderr}")
                    return None
            else:
                print(f"IPC套接字不存在: {self.socket_path}")
                return None
        except Exception as e:
            print(f"发送IPC命令错误: {e}")
            return None
    
    def _start_receive_thread(self):
        """启动接收线程，监听IINA状态变化"""
        if not self.receive_thread:
            self.running = True
            self.receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
            self.receive_thread.start()
    
    def _receive_loop(self):
        """接收IINA消息的循环"""
        while self.running and os.path.exists(self.socket_path):
            try:
                # 使用socat从IPC套接字读取消息
                process = subprocess.Popen(
                    ["socat", "-u", self.socket_path, "-"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1
                )
                
                # 逐行读取输出
                for line in iter(process.stdout.readline, ''):
                    if not self.running:
                        break
                    
                    if line:
                        self._process_message(line.strip())
                
                # 如果循环结束，等待一会儿再重试
                time.sleep(1)
                
            except Exception as e:
                print(f"IINA接收线程错误: {e}")
                time.sleep(1)  # 出错后等待一会儿再重试
    
    def _process_message(self, message):
        """处理来自IINA的消息"""
        try:
            data = json.loads(message)
            
            # 处理属性变更事件
            if 'event' in data and data['event'] == 'property-change':
                name = data.get('name')
                value = data.get('data')
                
                if name == 'time-pos' and value is not None:
                    position = int(value)
                    self.iina_position_changed.emit(position)
                    # 同步到DLNA服务器
                    if self.dlna_server and self.dlna_server.renderer:
                        position_str = f'{position // 3600:01d}:{(position % 3600) // 60:02d}:{position % 60:02d}'
                        self.dlna_server.renderer.set_state_position(position_str)
                
                elif name == 'duration' and value is not None:
                    duration = int(value)
                    self.iina_duration_changed.emit(duration)
                    # 同步到DLNA服务器
                    if self.dlna_server and self.dlna_server.renderer:
                        duration_str = f'{duration // 3600:01d}:{(duration % 3600) // 60:02d}:{duration % 60:02d}'
                        self.dlna_server.renderer.set_state_duration(duration_str)
                
                elif name == 'pause':
                    if self.dlna_server and self.dlna_server.renderer:
                        if value:
                            self.dlna_server.renderer.set_state_pause()
                        else:
                            self.dlna_server.renderer.set_state_play()
                
                elif name == 'volume' and value is not None:
                    volume = int(value)
                    self.iina_volume_changed.emit(volume)
                    # 同步到DLNA服务器
                    if self.dlna_server and self.dlna_server.renderer:
                        self.dlna_server.renderer.set_state_volume(volume)
            
            # 处理播放状态事件
            elif 'event' in data:
                event = data['event']
                
                if event == 'end-file':
                    # 播放结束
                    if self.dlna_server and self.dlna_server.renderer:
                        self.dlna_server.renderer.set_state_stop()
                
                elif event == 'start-file':
                    # 开始播放新文件
                    pass
                
                elif event == 'idle':
                    # 空闲状态
                    if self.dlna_server and self.dlna_server.renderer:
                        self.dlna_server.renderer.set_state_stop()
                
        except json.JSONDecodeError:
            print(f"无法解析IINA消息: {message}")
        except Exception as e:
            print(f"处理IINA消息错误: {e}")

    def _observe_properties(self):
        """设置需要观察的IINA属性"""
        properties = [
            "time-pos",     # 当前播放位置
            "duration",     # 总时长
            "pause",        # 暂停状态
            "volume",       # 音量
            "mute"          # 静音状态
        ]
        
        for prop in properties:
            self._send_ipc_command({
                "command": ["observe_property", self.command_counter, prop]
            })
            self.command_counter += 1

    def start_iina(self, url, fullscreen=None):
        """启动IINA播放器播放URL
        
        Args:
            url: 要播放的URL
            fullscreen: 是否全屏播放，None表示从设置文件读取
        """
        if not self.iina_cli_path:
            print("未找到IINA命令行工具")
            self.iina_connection_error.emit("未找到IINA命令行工具")
            return False
            
        try:
            # 检查是否启用全屏模式
            use_fullscreen = fullscreen
            if use_fullscreen is None:
                try:
                    if os.path.exists('settings.json'):
                        with open('settings.json', 'r', encoding='utf-8') as f:
                            settings = json.load(f)
                            use_fullscreen = settings.get('fullscreen', False)
                except Exception as e:
                    print(f"读取全屏设置失败: {e}")
                    use_fullscreen = False
            
            # 构建命令
            cmd = [self.iina_cli_path]
            
            # 添加参数
            cmd.append("--mpv-input-ipc-server=" + self.socket_path)
            cmd.append("--mpv-title=龙龙的电视机")
            
            
            # 添加URL
            cmd.append(url)
            
            # 启动IINA
            self.process = subprocess.Popen(cmd)
            
            # 等待IPC套接字创建
            start_time = time.time()
            while not os.path.exists(self.socket_path):
                if time.time() - start_time > 5.0:
                    print("等待IINA IPC套接字超时")
                    break
                time.sleep(0.1)
            
            # 给IINA一些启动时间
            time.sleep(1)
            
            # 启动接收线程
            self._start_receive_thread()
            
            # 设置观察属性
            self._observe_properties()
            
            return True
        except Exception as e:
            print(f"启动IINA失败: {e}")
            self.iina_connection_error.emit(str(e))
            return False
    
    def stop(self):
        """停止IINA播放器"""
        if self.process:
            try:
                # 停止接收线程
                self.running = False
                if self.receive_thread:
                    self.receive_thread.join(timeout=1)
                    self.receive_thread = None
                
                # 发送退出命令
                self._send_ipc_command({"command": ["quit"]})
                
                # 等待进程结束
                self.process.terminate()
                self.process.wait(timeout=3)
            except Exception as e:
                print(f"关闭IINA失败: {e}")
                # 如果超时，强制结束进程
                try:
                    self.process.kill()
                except:
                    pass
            finally:
                self.process = None
    
    def set_pause(self, pause=True):
        """设置暂停状态"""
        cmd = {"command": ["set_property", "pause", pause]}
        return self._send_ipc_command(cmd)
    
    def set_volume(self, volume):
        """设置音量（0-100）"""
        print("设置音量", volume)
        cmd = {"command": ["set_property", "volume", volume]}
        return self._send_ipc_command(cmd)
    
    def set_position(self, position):
        """设置播放位置（秒）"""
        cmd = {"command": ["seek", position, "absolute"]}
        return self._send_ipc_command(cmd)

# MPV控制器类，用于与MPV播放器通信
class MPVController(QObject):
    # 信号定义
    mpv_state_changed = Signal(str)  # 播放状态变化
    mpv_position_changed = Signal(int)  # 播放位置变化（秒）
    mpv_volume_changed = Signal(int)  # 音量变化
    mpv_duration_changed = Signal(int)  # 总时长变化（秒）
    mpv_connection_error = Signal(str)  # 连接错误
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_url = None
        self.socket_path = None
        self.socket = None
        self.process = None
        self.command_counter = 1
        self.running = False
        self.receive_thread = None
        self.dlna_server = None  # DLNA服务器引用
        
    def start_mpv(self, url):
        """启动MPV播放器并连接到IPC套接字"""
        # 如果MPV已经在运行，则直接加载新URL而不重启
        if self.process and self.socket:
            self.current_url = url
            print("MPV已经在运行，直接加载新URL", url)
            try:
                # 尝试直接加载新文件
                success = self.command({
                    "command": ["loadfile", url, "replace"]
                })
                if success:
                    return True
            except Exception as e:
                print(f"加载新URL失败，将重启MPV: {e}")
                # 如果加载失败，继续执行启动新实例的代码
                self.close()
                
        try:
            # 创建唯一的套接字路径
            if platform.system() == "Windows":
                # 在Windows上使用命名管道
                socket_id = random.randint(10000, 99999)
                self.socket_path = f"\\\\.\\pipe\\mpv-dlna-{socket_id}"
            else:
                # 在Unix系统上使用Unix域套接字
                socket_dir = tempfile.gettempdir()
                socket_id = random.randint(10000, 99999)
                self.socket_path = os.path.join(socket_dir, f"mpv-dlna-{socket_id}.sock")
                
            # 获取MPV路径
            mpv_path = set_mpv_default_path()
            
            # 检查是否启用全屏模式
            fullscreen = False
            try:
                if os.path.exists('settings.json'):
                    with open('settings.json', 'r', encoding='utf-8') as f:
                        settings = json.load(f)
                        fullscreen = settings.get('fullscreen', False)
            except Exception as e:
                print(f"读取全屏设置失败: {e}")
            
            # 启动MPV进程
            args = [
                mpv_path,
                f"--input-ipc-server={self.socket_path}",
                "--force-window=yes",
                "--keep-open=yes",
                "--ontop",
                "--no-terminal",
                "--title=龙龙的电视机",
            ]
            
            # 如果启用了全屏，添加全屏参数
            if fullscreen:
                args.append("--fullscreen")
                
            # 添加URL
            args.append(url)
            
            self.process = subprocess.Popen(args)
            
            # 等待套接字文件创建
            self._wait_for_socket_ready()
            
            # 连接到套接字
            self._connect_to_socket()
            
            # 开始接收线程
            self.running = True
            self.receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
            self.receive_thread.start()
            
            # 设置观察属性
            self._observe_properties()
            
            return True
        except Exception as e:
            print(f"启动MPV失败: {e}")
            self.mpv_connection_error.emit(str(e))
            return False
    
    def _wait_for_socket_ready(self, timeout=5.0):
        """等待套接字文件准备好"""
        if platform.system() != "Windows":
            start_time = time.time()
            while not os.path.exists(self.socket_path):
                if time.time() - start_time > timeout:
                    raise TimeoutError("等待MPV套接字文件超时")
                time.sleep(0.1)
            # 确保有足够时间进行初始化
            time.sleep(0.5)
    
    def _connect_to_socket(self):
        """连接到MPV的IPC套接字"""
        if platform.system() == "Windows":
            # Windows上使用命名管道
            import win32file
            import pywintypes
            try:
                self.socket = win32file.CreateFile(
                    self.socket_path,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0, None,
                    win32file.OPEN_EXISTING,
                    0, None
                )
            except pywintypes.error as e:
                raise ConnectionError(f"无法连接到MPV命名管道: {e}")
        else:
            # Unix系统上使用Unix域套接字
            self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                self.socket.connect(self.socket_path)
            except socket.error as e:
                raise ConnectionError(f"无法连接到MPV套接字: {e}")
    
    def _receive_loop(self):
        """接收MPV消息的循环"""
        buffer = b""
        while self.running:
            try:
                # 接收数据
                if platform.system() == "Windows":
                    import win32file
                    hr, data = win32file.ReadFile(self.socket, 4096)
                    if hr != 0:
                        break
                else:
                    data = self.socket.recv(4096)
                    if not data:
                        break
                
                # 处理数据
                buffer += data
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    if line:
                        self._process_message(line.decode('utf-8', errors='replace'))
                        
            except Exception as e:
                print(f"MPV接收线程错误: {e}")
                break
        
        # 关闭套接字
        self._close_socket()
    
    def _process_message(self, message):
        """处理来自MPV的消息"""
        try:
            data = json.loads(message)
            
            # 处理属性变更事件
            if 'event' in data and data['event'] == 'property-change':
                name = data.get('name')
                value = data.get('data')
                
                if name == 'time-pos' and value is not None:
                    position = int(value)
                    self.mpv_position_changed.emit(position)
                    # 同步到DLNA服务器
                    if self.dlna_server and self.dlna_server.renderer:
                        position_str = f'{position // 3600:01d}:{(position % 3600) // 60:02d}:{position % 60:02d}'
                        self.dlna_server.renderer.set_state_position(position_str)
                
                elif name == 'duration' and value is not None:
                    duration = int(value)
                    self.mpv_duration_changed.emit(duration)
                    # 同步到DLNA服务器
                    if self.dlna_server and self.dlna_server.renderer:
                        duration_str = f'{duration // 3600:01d}:{(duration % 3600) // 60:02d}:{duration % 60:02d}'
                        self.dlna_server.renderer.set_state_duration(duration_str)
                
                elif name == 'pause':
                    if self.dlna_server and self.dlna_server.renderer:
                        if value:
                            self.dlna_server.renderer.set_state_pause()
                        else:
                            self.dlna_server.renderer.set_state_play()
                
                elif name == 'volume' and value is not None:
                    volume = int(value)
                    self.mpv_volume_changed.emit(volume)
                    # 同步到DLNA服务器
                    if self.dlna_server and self.dlna_server.renderer:
                        self.dlna_server.renderer.set_state_volume(volume)
            
            # 处理播放状态事件
            elif 'event' in data:
                event = data['event']
                
                if event == 'end-file':
                    # 播放结束
                    if self.dlna_server and self.dlna_server.renderer:
                        self.dlna_server.renderer.set_state_stop()
                
                elif event == 'start-file':
                    # 开始播放新文件
                    pass
                
                elif event == 'idle':
                    # 空闲状态
                    if self.dlna_server and self.dlna_server.renderer:
                        self.dlna_server.renderer.set_state_stop()
                
        except json.JSONDecodeError:
            print(f"无法解析MPV消息: {message}")
        except Exception as e:
            print(f"处理MPV消息错误: {e}")
    
    def _observe_properties(self):
        """设置需要观察的MPV属性"""
        properties = [
            "time-pos",     # 当前播放位置
            "duration",     # 总时长
            "pause",        # 暂停状态
            "volume",       # 音量
            "mute"          # 静音状态
        ]
        
        for prop in properties:
            self.command({
                "command": ["observe_property", self.command_counter, prop]
            })
            self.command_counter += 1
    
    def command(self, command_dict):
        """发送命令到MPV"""
        try:
            if not self.socket:
                return False
                
            # 添加请求ID
            if 'request_id' not in command_dict:
                command_dict['request_id'] = self.command_counter
                self.command_counter += 1
            
            # 编码命令
            command_json = json.dumps(command_dict) + '\n'
            command_bytes = command_json.encode('utf-8')
            
            # 发送命令
            if platform.system() == "Windows":
                import win32file
                win32file.WriteFile(self.socket, command_bytes)
            else:
                self.socket.sendall(command_bytes)
                
            return True
        except Exception as e:
            print(f"MPV命令发送错误: {e}")
            return False
    
    def set_position(self, position):
        """设置播放位置（秒）"""
        return self.command({
            "command": ["seek", position, "absolute"]
        })
    
    def set_pause(self, pause=True):
        """设置暂停状态"""
        return self.command({
            "command": ["set_property", "pause", pause]
        })
    
    def set_volume(self, volume):
        """设置音量（0-100）"""
        print("设置音量", volume)
        return self.command({
            "command": ["set_property", "volume", volume]
        })
    
    def set_mute(self, mute=True):
        """设置静音状态"""
        print("设置静音", mute)
        return self.command({
            "command": ["set_property", "mute", mute]
        })
    
    def set_title(self, title):
        """设置播放器窗口标题"""
        print(f"设置窗口标题: {title}")
        return self.command({
            "command": ["set_property", "title", title]
        })
    
    def stop(self):
        """停止播放并关闭MPV"""
        if self.process:
            # 设置当前URL为None
            print("停止播放")
            #self.set_pause(True)

            self.current_url = None

            # 使用线程而不是QTimer来延迟关闭
            def delayed_check():
                time.sleep(10)  # 等待5秒
                # 使用Qt的信号槽机制在主线程中安全地调用check_and_close
                QMetaObject.invokeMethod(self, "check_and_close", Qt.QueuedConnection)

            # 启动后台线程
            threading.Thread(target=delayed_check, daemon=True).start()

    # 将check_and_close方法标记为槽，以便可以从其他线程调用
    @Slot()
    def check_and_close(self):
        """检查是否有新的URL投递进来"""
        print("检查是否有新的URL投递进来", self.current_url)

        if self.current_url is None:
            self.close()

    def close(self):
        """停止播放并关闭MPV"""
        if self.process:
            try:
                # 先发送退出命令
                self.command({
                    "command": ["quit"]
                })
                # 等待进程结束
                self.process.terminate()
                self.process.wait(timeout=3)
            except Exception as e:
                print(f"停止MPV进程错误: {e}")
                # 如果超时，强制结束进程
                try:
                    self.process.kill()
                except:
                    pass
            finally:
                self.process = None
        
        # 停止接收线程
        self.running = False
        if self.receive_thread:
            self.receive_thread.join(timeout=1)
            self.receive_thread = None
        
        # 关闭套接字
        self._close_socket()
    
    def _close_socket(self):
        """关闭套接字连接"""
        if self.socket:
            try:
                if platform.system() == "Windows":
                    import win32file
                    win32file.CloseHandle(self.socket)
                else:
                    self.socket.close()
            except Exception as e:
                print(f"关闭MPV套接字错误: {e}")
            finally:
                self.socket = None
        
        # 删除套接字文件
        if platform.system() != "Windows" and self.socket_path and os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except:
                pass

# 自定义MPV DLNA渲染器，用于控制MPV播放器
class MPVDLNARenderer:
    def __init__(self, dlna_server):
        self.dlna_server = dlna_server
        self.current_url = None
        self.current_title = None
        self.current_position = "00:00:00"
        self.current_duration = "00:00:00"
        self.current_volume = 100
        self.current_mute = False
        self.is_playing = False
        self.mpv_controller = None  # 将由外部设置
        self.iina_controller = None  # 将由外部设置
        
    def set_mpv_controller(self, controller):
        """设置MPV控制器引用"""
        self.mpv_controller = controller
    
    def set_iina_controller(self, controller):
        """设置IINA控制器引用"""
        self.iina_controller = controller
        
    def get_player_type(self):
        """获取默认播放器类型"""
        try:
            if os.path.exists('settings.json'):
                with open('settings.json', 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                    return settings.get('default_player', 'mpv')
        except Exception as e:
            print(f"读取默认播放器设置失败: {e}")
        return 'mpv'  # 默认使用MPV
        
    def set_media_url(self, uri, title=""):
        """设置媒体URL"""
        self.current_url = uri
        self.current_title = title
        # 获取默认播放器类型
        player_type = self.get_player_type()
        # 播放成功标志
        play_success = False
        
        # 检查是否启用全屏模式
        fullscreen = False
        try:
            if os.path.exists('settings.json'):
                with open('settings.json', 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                    fullscreen = settings.get('fullscreen', False)
        except Exception as e:
            print(f"读取全屏设置失败: {e}")
        
        # 根据默认播放器类型选择使用哪个控制器
        if player_type == 'iina' and platform.system() == "Darwin" and self.iina_controller:
            # 使用IINA控制器，并传递全屏参数
            play_success = self.iina_controller.start_iina(uri, fullscreen)
        elif self.mpv_controller:
            # 使用MPV控制器
            play_success = self.mpv_controller.start_mpv(uri)
        
        # 如果播放成功，设置状态为播放
        if play_success:
            self.is_playing = True
            self.set_state_play()
      

        return play_success
    
    def set_media_pause(self):
        """暂停播放"""
        player_type = self.get_player_type()
        
        if player_type == 'iina' and platform.system() == "Darwin" and self.iina_controller:
            self.iina_controller.set_pause(True)
        elif self.mpv_controller and self.mpv_controller.process:
            self.mpv_controller.set_pause(True)
            
        self.set_state_pause()
    
    def set_media_resume(self):
        """恢复播放"""
        player_type = self.get_player_type()
        
        if player_type == 'iina' and platform.system() == "Darwin" and self.iina_controller:
            self.iina_controller.set_pause(False)
        elif self.mpv_controller and self.mpv_controller.process:
            self.mpv_controller.set_pause(False)
            
        self.set_state_play()
    
    def set_media_stop(self):
        """停止播放"""
        player_type = self.get_player_type()
        
        if player_type == 'iina' and platform.system() == "Darwin" and self.iina_controller:
            self.iina_controller.stop()
        elif self.mpv_controller and self.mpv_controller.process:
            self.mpv_controller.stop()
            
        self.set_state_stop()
    
    def set_media_position(self, position):
        """设置播放位置"""
        # 解析时间字符串为秒
        parts = position.split(':')
        if len(parts) == 3:
            seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            
            player_type = self.get_player_type()
            
            if player_type == 'iina' and platform.system() == "Darwin" and self.iina_controller:
                self.iina_controller.set_position(seconds)
            elif self.mpv_controller and self.mpv_controller.process:
                self.mpv_controller.set_position(seconds)
    
    def set_media_volume(self, volume):
        """设置音量"""
        print("设置音量", volume)
        self.current_volume = volume
        
        player_type = self.get_player_type()
        
        if player_type == 'iina' and platform.system() == "Darwin" and self.iina_controller:
            self.iina_controller.set_volume(volume)
        elif self.mpv_controller and self.mpv_controller.process:
            self.mpv_controller.set_volume(volume)
            
        self.set_state_volume(volume)
    
    def set_media_mute(self, mute):
        """设置静音"""
        print("设置静音", mute)
        self.current_mute = mute
        
        player_type = self.get_player_type()
        
        if player_type == 'iina' and platform.system() == "Darwin" and self.iina_controller:
            # IINA没有直接的mute命令，可以设置音量为0来达到相同效果
            if mute:
                self.iina_controller.set_volume(0)
            else:
                self.iina_controller.set_volume(self.current_volume)
        elif self.mpv_controller and self.mpv_controller.process:
            self.mpv_controller.set_mute(mute)
            
        self.set_state_mute(mute)

    # 以下方法用于更新DLNA协议状态
    
    def set_state_position(self, position):
        """设置播放位置状态"""
        self.current_position = position
        if self.dlna_server and self.dlna_server.renderer:
            if hasattr(self.dlna_server.renderer, 'protocol'):
                if self.dlna_server.renderer.protocol:
                    self.dlna_server.renderer.protocol.set_state_position(position)
    
    def set_state_duration(self, duration):
        """设置媒体时长状态"""
        self.current_duration = duration
        if self.dlna_server and self.dlna_server.renderer:
            if hasattr(self.dlna_server.renderer, 'protocol'):
                if self.dlna_server.renderer.protocol:
                    self.dlna_server.renderer.protocol.set_state_duration(duration)
    
    def set_state_pause(self):
        """设置暂停状态"""
        self.is_playing = False
        if self.dlna_server and self.dlna_server.renderer:
            if hasattr(self.dlna_server.renderer, 'protocol'):
                if self.dlna_server.renderer.protocol:
                    self.dlna_server.renderer.protocol.set_state_pause()
    
    def set_state_play(self):
        """设置播放状态"""
        self.is_playing = True
        if self.dlna_server and self.dlna_server.renderer:
            if hasattr(self.dlna_server.renderer, 'protocol'):
                if self.dlna_server.renderer.protocol:
                    self.dlna_server.renderer.protocol.set_state_play()
    
    def set_state_stop(self):
        """设置停止状态"""
        self.is_playing = False
        if self.dlna_server and self.dlna_server.renderer:
            if hasattr(self.dlna_server.renderer, 'protocol'):
                if self.dlna_server.renderer.protocol:
                    self.dlna_server.renderer.protocol.set_state_stop()
    
    def set_state_volume(self, volume):
        """设置音量状态"""
        print("设置音量", volume)
        if self.dlna_server and self.dlna_server.renderer:
            if hasattr(self.dlna_server.renderer, 'protocol'):
                if self.dlna_server.renderer.protocol:
                    self.dlna_server.renderer.protocol.set_state_volume(volume)
    
    def set_state_mute(self, mute):
        """设置静音状态"""
        print("设置静音", mute)
        if self.dlna_server and self.dlna_server.renderer:
            if hasattr(self.dlna_server.renderer, 'protocol'):
                if self.dlna_server.renderer.protocol:
                    self.dlna_server.renderer.protocol.set_state_mute(mute)

# 全局变量
window = None 