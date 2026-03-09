#!/usr/bin/env python3
import glob
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Optional, Tuple

from prompt_toolkit import prompt
from prompt_toolkit.shortcuts import clear
from prompt_toolkit.validation import Validator
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text
import readchar

console = Console()
DOWNLOAD_PATH = "/Users/menggq/Downloads"


@dataclass
class VideoFormat:
    format_id: str
    ext: str
    resolution: str
    fps: str
    filesize: str
    vcodec: str
    acodec: str
    has_video: bool
    has_audio: bool
    quality_note: str


@dataclass
class VideoInfo:
    title: str
    uploader: str
    duration: str
    formats: List[VideoFormat]
    requires_login: bool
    login_hint: str = ""
    is_playlist: bool = False
    playlist_count: int = 0
    playlist_title: str = ""


@dataclass
class PlaylistItem:
    index: int
    url: str
    title: str
    duration: str
    uploader: str
    selected: bool = True


@dataclass
class PlaylistInfo:
    title: str
    uploader: str
    video_count: int
    items: List[PlaylistItem]


class YtDlpTUI:
    def __init__(self):
        self.url: str = ""
        self.current_download_file: Optional[str] = None

    def print_header(self):
        header = Panel(
            Text("🎬 yt-dlp TUI - Video Downloader", style="bold cyan", justify="center"),
            border_style="cyan"
        )
        console.print(header)
        console.print("[dim]Enter video URL or 'clean' to remove temp files, 'q' to quit[/dim]")
        console.print()

    def get_url(self) -> str:
        self.print_header()
        return prompt(
            "🔗 Enter video URL: ",
            validator=Validator.from_callable(
                lambda x: bool(x.strip()),
                error_message="URL cannot be empty"
            )
        ).strip()

    def find_temp_files(self) -> List[str]:
        temp_files = []
        patterns = [
            os.path.join(DOWNLOAD_PATH, "*.aria2"),
            os.path.join(DOWNLOAD_PATH, "*.part"),
            os.path.join(DOWNLOAD_PATH, "*.part-*"),
            os.path.join(DOWNLOAD_PATH, "*.temp"),
            os.path.join(DOWNLOAD_PATH, "*.ytdl"),
            os.path.join(DOWNLOAD_PATH, "*.f*.*"),
        ]
        for pattern in patterns:
            temp_files.extend(glob.glob(pattern))
        
        # Also find HLS fragment files (*.mp4.part-Frag*)
        all_files = os.listdir(DOWNLOAD_PATH) if os.path.exists(DOWNLOAD_PATH) else []
        for f in all_files:
            if ".part-Frag" in f or f.endswith((".part", ".temp", ".ytdl")):
                full_path = os.path.join(DOWNLOAD_PATH, f)
                if full_path not in temp_files:
                    temp_files.append(full_path)
        
        return list(set(temp_files))

    def clean_temp_files(self, files: Optional[List[str]] = None) -> int:
        if files is None:
            files = self.find_temp_files()
        
        cleaned = 0
        for f in files:
            try:
                os.remove(f)
                cleaned += 1
            except Exception:
                pass
        return cleaned

    def prompt_clean_on_cancel(self):
        temp_files = self.find_temp_files()
        if not temp_files:
            return
        
        total_size = sum(os.path.getsize(f) for f in temp_files if os.path.exists(f))
        size_str = self.format_size(total_size)
        
        console.print()
        console.print(Panel(
            f"Found [yellow]{len(temp_files)}[/yellow] temporary files ({size_str})\n"
            f"These are incomplete downloads that can be resumed.\n\n"
            "Do you want to clean them?",
            title="🗑️  Cleanup",
            border_style="yellow"
        ))
        console.print()
        
        console.print("[bold cyan]Options:[/bold cyan]")
        console.print("  [green]k[/green] - Keep files (for resume later)")
        console.print("  [green]c[/green] - Clean all temp files")
        console.print()
        
        choice = prompt("Select [k]: ", default="k").strip().lower()
        
        if choice == "c":
            cleaned = self.clean_temp_files(temp_files)
            console.print(f"[green]Cleaned {cleaned} temporary files.[/green]")
        else:
            console.print("[cyan]Temp files kept for later resume.[/cyan]")

    def format_size(self, size_bytes: int) -> str:
        size = float(size_bytes)
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    def get_quality_score(self, fmt: VideoFormat) -> int:
        if not fmt.has_video:
            return 0
        match = re.search(r"(\d+)p", fmt.resolution)
        if match:
            return int(match.group(1))
        match = re.search(r"x(\d+)", fmt.resolution)
        if match:
            return int(match.group(1))
        return 0

    def extract_login_hint(self, stderr: str) -> str:
        stderr_lower = stderr.lower()
        if "age-restricted" in stderr_lower:
            return "This video is age-restricted and requires login"
        elif "private" in stderr_lower:
            return "This is a private video and requires login"
        elif "members" in stderr_lower:
            return "This video is for members only"
        else:
            return "Login required to access this video"

    def parse_video_info(self, data: dict, requires_login: bool = False, login_hint: str = "") -> VideoInfo:
        formats = []
        for fmt in data.get("formats", []):
            vcodec_val = fmt.get("vcodec")
            acodec_val = fmt.get("acodec")
            
            has_video = vcodec_val is not None and vcodec_val != "none"
            has_audio = acodec_val is not None and acodec_val != "none"
            
            # Also check video_ext for direct video links
            if not has_video and fmt.get("video_ext") and fmt.get("video_ext") != "none":
                has_video = True
            # Check protocol - HLS usually has both video and audio
            protocol = fmt.get("protocol", "")
            if protocol.startswith("m3u8") or protocol == "hls":
                if not has_video and not has_audio:
                    has_video = True
                    has_audio = True

            if not has_video and not has_audio:
                continue

            filesize = fmt.get("filesize") or fmt.get("filesize_approx")
            tbr = fmt.get("tbr")
            if filesize:
                filesize_str = self.format_size(filesize)
            elif tbr:
                # Estimate filesize from bitrate (tbr is in kbps)
                duration = data.get("duration", 0)
                if duration:
                    estimated = (tbr * 1000 * duration) / 8
                    filesize_str = f"~{self.format_size(estimated)}"
                else:
                    filesize_str = "-"
            else:
                filesize_str = "-"

            height = fmt.get("height") or 0
            width = fmt.get("width") or 0
            format_id = fmt.get("format_id", "")
            
            # Get resolution from format_id if not available
            if height and width:
                resolution = f"{width}x{height}"
            elif height:
                resolution = f"{height}p"
            elif format_id and "p" in format_id.lower():
                resolution = format_id.upper()
            elif format_id.startswith("hls-"):
                # Extract resolution from HLS format
                if "1080" in format_id or tbr and tbr > 3000:
                    resolution = "1080p"
                elif "720" in format_id or tbr and tbr > 1500:
                    resolution = "720p"
                elif "480" in format_id or tbr and tbr > 800:
                    resolution = "480p"
                else:
                    resolution = "240p"
            else:
                resolution = "video" if has_video else "audio"

            fps_value = fmt.get("fps")
            fps_str = str(fps_value) if fps_value else ""

            if has_video and has_audio:
                quality_note = "📹🎵 Video+Audio"
            elif has_video:
                quality_note = "📹 Video"
            else:
                quality_note = "🎵 Audio"

            # Handle None values for codecs - show "-" instead of "unknown"
            vcodec_str = (vcodec_val or "-")[:20] if vcodec_val else "-"
            acodec_str = (acodec_val or "-")[:20] if acodec_val else "-"

            formats.append(VideoFormat(
                format_id=str(format_id or "unknown"),
                ext=fmt.get("ext", "unknown"),
                resolution=resolution,
                fps=fps_str,
                filesize=filesize_str,
                vcodec=vcodec_str,
                acodec=acodec_str,
                has_video=has_video,
                has_audio=has_audio,
                quality_note=quality_note
            ))

        formats.sort(key=lambda x: self.get_quality_score(x), reverse=True)

        duration = data.get("duration", 0)
        if duration:
            duration_val = int(duration)
            hours = duration_val // 3600
            minutes = (duration_val % 3600) // 60
            seconds = duration_val % 60
            if hours > 0:
                duration_str = f"{hours}:{minutes:02d}:{seconds:02d}"
            else:
                duration_str = f"{minutes}:{seconds:02d}"
        else:
            duration_str = "Unknown"

        return VideoInfo(
            title=data.get("title", "Unknown"),
            uploader=data.get("uploader", data.get("channel", "Unknown")),
            duration=duration_str,
            formats=formats,
            requires_login=requires_login,
            login_hint=login_hint
        )

    def fetch_info(self, url: str, extra_args: Optional[List[str]] = None) -> Optional[VideoInfo]:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task("Fetching video info...", total=None)

            try:
                cmd = ["yt-dlp", "--dump-json", "--no-warnings", "--socket-timeout", "30"]
                if extra_args:
                    cmd.extend(extra_args)
                cmd.append(url)

                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

                if result.returncode != 0:
                    stderr = result.stderr.lower()
                    requires_login = any(x in stderr for x in [
                        "sign in", "login", "authentication", "confirm you're not a bot",
                        "age-restricted", "private video", "members only"
                    ])

                    if requires_login:
                        login_hint = self.extract_login_hint(stderr)
                        return VideoInfo(
                            title="Login Required",
                            uploader="Unknown",
                            duration="Unknown",
                            formats=[],
                            requires_login=True,
                            login_hint=login_hint
                        )
                    else:
                        console.print(f"[red]Error: {result.stderr}[/red]")
                        return None

                data = json.loads(result.stdout.strip().split("\n")[-1])
                progress.remove_task(task)
                
                playlist_count = data.get("playlist_count", 0)
                if playlist_count and playlist_count > 1:
                    return VideoInfo(
                        title=data.get("playlist_title") or data.get("title", "Unknown"),
                        uploader=data.get("uploader", data.get("channel", "Unknown")),
                        duration=f"{playlist_count} videos",
                        formats=[],
                        requires_login=False,
                        is_playlist=True,
                        playlist_count=playlist_count,
                        playlist_title=data.get("playlist_title", "")
                    )
                
                return self.parse_video_info(data)

            except subprocess.TimeoutExpired:
                console.print("[red]Error: Request timed out[/red]")
                return None
            except json.JSONDecodeError:
                console.print("[red]Error: Failed to parse video info[/red]")
                return None
            except Exception as e:
                console.print(f"[red]Error: {str(e)}[/red]")
                return None

    def is_playlist(self, url: str) -> bool:
        playlist_patterns = [
            "playlist?list=",
            "playlist?",
            "/playlist/",
            "album?list=",
            "course?list=",
            "series?list=",
            "?list=",
            "&list=",
        ]
        url_lower = url.lower()
        return any(p in url_lower for p in playlist_patterns)

    def fetch_playlist_info(self, url: str, extra_args: Optional[List[str]] = None) -> Optional[PlaylistInfo]:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task("Fetching playlist info...", total=None)

            try:
                cmd = [
                    "yt-dlp", "--flat-playlist", "--dump-json", "--no-warnings",
                    "--socket-timeout", "30"
                ]
                if extra_args:
                    cmd.extend(extra_args)
                cmd.append(url)

                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

                if result.returncode != 0:
                    console.print(f"[red]Error: {result.stderr}[/red]")
                    return None

                lines = result.stdout.strip().split("\n")
                if not lines or not lines[0].strip():
                    return None

                first_data = json.loads(lines[0])
                playlist_title = first_data.get("playlist_title", first_data.get("title", "Unknown"))
                playlist_uploader = first_data.get("playlist_uploader", first_data.get("uploader", "Unknown"))
                video_count = first_data.get("playlist_count", len(lines))

                items = []
                for idx, line in enumerate(lines, 1):
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        video_url = data.get("url") or data.get("webpage_url") or ""
                        
                        items.append(PlaylistItem(
                            index=idx,
                            url=video_url,
                            title=f"Video {idx}",
                            duration="-",
                            uploader="-",
                            selected=True
                        ))
                    except json.JSONDecodeError:
                        continue

                progress.remove_task(task)
                
                playlist = PlaylistInfo(
                    title=playlist_title,
                    uploader=playlist_uploader,
                    video_count=video_count,
                    items=items
                )
                
                return playlist

            except subprocess.TimeoutExpired:
                console.print("[red]Error: Request timed out[/red]")
                return None
            except Exception as e:
                console.print(f"[red]Error: {str(e)}[/red]")
                return None

    def fetch_video_titles_parallel(self, url: str, playlist: PlaylistInfo, 
                                     extra_args: Optional[List[str]] = None,
                                     max_workers: int = 10) -> None:
        def fetch_single_title(video_url: str) -> Tuple[str, str]:
            try:
                cmd = ["yt-dlp", "--dump-json", "--no-warnings", "--socket-timeout", "15"]
                if extra_args:
                    cmd.extend(extra_args)
                cmd.append(video_url)
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    data = json.loads(result.stdout.strip().split("\n")[-1])
                    title = data.get("title", "Unknown")
                    duration = data.get("duration")
                    if duration:
                        duration_str = f"{int(duration // 60)}:{int(duration % 60):02d}"
                    else:
                        duration_str = "-"
                    return title, duration_str
            except:
                pass
            return "Unknown", "-"

        console.print("[cyan]Loading video titles in parallel (this may take a moment)...[/cyan]")
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {}
            for item in playlist.items:
                if item.url:
                    future = executor.submit(fetch_single_title, item.url)
                    future_to_idx[future] = item.index - 1
            
            completed = 0
            total = len(future_to_idx)
            
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    title, duration = future.result()
                    playlist.items[idx].title = title
                    playlist.items[idx].duration = duration
                except:
                    pass
                
                completed += 1
                if completed % 5 == 0 or completed == total:
                    console.print(f"[dim]Loaded {completed}/{total} video titles...[/dim]")

    def display_playlist_interactive(self, playlist: PlaylistInfo, current_pos: int, page_offset: int, page_size: int = 20):
        clear()
        self.print_header()

        info_text = Text()
        info_text.append("📺 Playlist: ", style="bold")
        info_text.append(f"{playlist.title}\n", style="cyan")
        info_text.append("📊 Videos: ", style="bold")
        info_text.append(f"{playlist.video_count}", style="yellow")

        console.print(Panel(info_text, title="📋 Select Videos to Download", border_style="blue"))
        console.print()

        start = page_offset
        end = min(start + page_size, len(playlist.items))
        display_items = playlist.items[start:end]

        for i, item in enumerate(display_items):
            actual_idx = start + i
            is_current = (actual_idx == current_pos)
            
            check = "✓" if item.selected else " "
            
            title_display = item.title[:50] if len(item.title) <= 50 else item.title[:47] + "..."
            duration_display = item.duration if item.duration != "-" else "   -   "
            
            line = f"{'>' if is_current else ' '} [{check}] {item.index:2d}. {title_display:<53} {duration_display:>8}"
            
            if is_current:
                console.print(f"[black on white]{line}[/black on white]")
            elif item.selected:
                console.print(f"[green]{line}[/green]")
            else:
                console.print(f"[dim]{line}[/dim]")

        console.print()
        selected_count = sum(1 for item in playlist.items if item.selected)
        console.print(f"[cyan]Selected: {selected_count}/{playlist.video_count} videos[/cyan]")
        
        total_pages = (len(playlist.items) + page_size - 1) // page_size
        current_page = current_pos // page_size + 1
        console.print(f"[dim]Page {current_page}/{total_pages}[/dim]")
        
        console.print()
        console.print("[bold]Controls:[/bold] [green]↑/↓[/green] Move  [green]Space[/green] Toggle  [green]Enter[/green] Done  [green]q[/green] Quit")

    def select_playlist_items(self, playlist: PlaylistInfo) -> Tuple[bool, str]:
        current_pos = 0
        page_size = 20

        while True:
            page_offset = (current_pos // page_size) * page_size
            self.display_playlist_interactive(playlist, current_pos, page_offset, page_size)

            try:
                key = readchar.readkey()
            except:
                key = input()

            if key == readchar.key.UP:
                current_pos = max(0, current_pos - 1)
            elif key == readchar.key.DOWN:
                current_pos = min(len(playlist.items) - 1, current_pos + 1)
            elif key == readchar.key.PAGE_UP or key == 'p':
                current_pos = max(0, current_pos - page_size)
            elif key == readchar.key.PAGE_DOWN or key == 'n':
                current_pos = min(len(playlist.items) - 1, current_pos + page_size)
            elif key == ' ':
                playlist.items[current_pos].selected = not playlist.items[current_pos].selected
            elif key == readchar.key.ENTER or key == '\r' or key == '\n':
                selected_indices = [str(i.index) for i in playlist.items if i.selected]
                if not selected_indices:
                    console.print("\n[red]No videos selected![/red]")
                    input("Press Enter to continue...")
                    continue
                if len(selected_indices) == len(playlist.items):
                    return True, ""
                return True, ",".join(selected_indices)
            elif key == 'a':
                for item in playlist.items:
                    item.selected = True
            elif key == 'c':
                for item in playlist.items:
                    item.selected = False
            elif key == 'q' or key == readchar.key.ESC:
                return False, ""

        return False, ""

    def download_playlist(self, url: str, playlist: PlaylistInfo, extra_args: List[str], 
                          format_id: str, item_spec: str = ""):
        output_path = DOWNLOAD_PATH
        playlist_folder = re.sub(r'[<>:"/\\|?*]', '_', playlist.title)
        playlist_path = os.path.join(output_path, playlist_folder)
        
        console.print()
        if item_spec:
            console.print(Panel(
                f"Starting playlist download...\n"
                f"Items: [cyan]{item_spec}[/cyan]\n"
                f"Format: [cyan]{format_id}[/cyan]\n"
                f"Save to: [yellow]{playlist_path}[/yellow]",
                title="⬇️  Playlist Download",
                border_style="green"
            ))
        else:
            console.print(Panel(
                f"Starting playlist download...\n"
                f"Videos: [cyan]{playlist.video_count}[/cyan]\n"
                f"Format: [cyan]{format_id}[/cyan]\n"
                f"Save to: [yellow]{playlist_path}[/yellow]",
                title="⬇️  Playlist Download",
                border_style="green"
            ))
        console.print()

        console.print("[bold cyan]Download Method:[/bold cyan]")
        console.print("  [green]1[/green] - Multi-threaded (yt-dlp native, 16 threads) [default]")
        console.print("  [green]2[/green] - Multi-threaded (aria2c, 16 threads)")
        console.print("  [green]3[/green] - Single thread")
        console.print()

        choice = prompt("Select method [1]: ", default="1").strip()

        base_cmd = [
            "yt-dlp",
            f"-o{playlist_path}/%(playlist_index)03d - %(title)s.%(ext)s",
            "--continue",
            "--part",
            "--no-playlist-reverse",
        ]
        
        if item_spec:
            base_cmd.extend(["-I", item_spec])

        if choice in ("1", ""):
            base_cmd.extend(["-N", "16"])
            console.print("\n[cyan]Using yt-dlp native multi-thread (16 fragments)...[/cyan]")
        elif choice == "2":
            aria2c_args = [
                "--external-downloader", "aria2c",
                "--external-downloader-args", "-x 16 -s 16 -k 1M --continue=true --file-allocation=none"
            ]
            base_cmd.extend(aria2c_args)
            console.print("\n[cyan]Using aria2c with 16 threads...[/cyan]")
        else:
            console.print("\n[cyan]Using single thread...[/cyan]")

        if format_id != "best":
            if format_id == "worst":
                base_cmd.extend(["-f", "worst"])
            elif format_id == "bestaudio":
                base_cmd.extend(["-x", "--audio-format", "best"])
            else:
                base_cmd.extend(["-f", format_id])

        base_cmd.extend(extra_args)
        base_cmd.append(url)

        process = None
        try:
            process = subprocess.Popen(
                base_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            stdout = process.stdout
            if stdout:
                for line in stdout:
                    line = line.strip()
                    if line:
                        if "error" in line.lower():
                            console.print(f"[red]{line}[/red]")
                        elif "100%" in line or "complete" in line.lower():
                            console.print(f"[green]{line}[/green]")
                        elif "has already" in line.lower():
                            console.print(f"[yellow]{line}[/yellow]")
                        elif "Downloading" in line:
                            console.print(f"[cyan]{line}[/cyan]")
                        else:
                            console.print(line)

            process.wait()

            if process.returncode == 0:
                console.print()
                console.print(Panel(
                    "✅ Playlist download completed!",
                    border_style="green"
                ))
            else:
                console.print()
                console.print(Panel(
                    f"❌ Download failed (code: {process.returncode})",
                    border_style="red"
                ))

        except KeyboardInterrupt:
            console.print("\n[yellow]Download cancelled by user[/yellow]")
            if process:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
        except Exception as e:
            console.print(f"\n[red]Error: {str(e)}[/red]")

        temp_files = self.find_temp_files()
        if temp_files:
            cleaned = self.clean_temp_files(temp_files)
            if cleaned > 0:
                console.print(f"[dim]Cleaned {cleaned} temp files[/dim]")

    def display_video_info(self, info: VideoInfo):
        clear()
        self.print_header()

        info_text = Text()
        info_text.append("📺 Title: ", style="bold")
        info_text.append(f"{info.title}\n", style="cyan")
        info_text.append("👤 Uploader: ", style="bold")
        info_text.append(f"{info.uploader}\n", style="green")
        info_text.append("⏱️  Duration: ", style="bold")
        info_text.append(f"{info.duration}", style="yellow")

        console.print(Panel(info_text, title="Video Info", border_style="blue"))
        console.print()

    def display_formats(self, info: VideoInfo) -> List[VideoFormat]:
        if not info.formats:
            console.print("[yellow]No formats available[/yellow]")
            return []

        table = Table(title="Available Formats", show_header=True, header_style="bold magenta")
        table.add_column("#", style="cyan", width=4, justify="right")
        table.add_column("ID", style="green", width=8)
        table.add_column("Resolution", style="blue", width=12)
        table.add_column("Format", style="yellow", width=8)
        table.add_column("Size", style="cyan", width=12)
        table.add_column("Codecs", style="magenta", width=25)
        table.add_column("Type", style="green")

        seen_resolutions = set()
        unique_formats = []

        for fmt in info.formats:
            key = (fmt.resolution, fmt.ext, fmt.has_audio)
            if key not in seen_resolutions:
                seen_resolutions.add(key)
                unique_formats.append(fmt)

        display_formats = unique_formats[:20]

        for idx, fmt in enumerate(display_formats, 1):
            vcodec_short = fmt.vcodec.split(".")[0] if "." in fmt.vcodec else fmt.vcodec[:10]
            acodec_short = fmt.acodec.split(".")[0] if "." in fmt.acodec else fmt.acodec[:10]
            codecs = f"V:{vcodec_short}"
            if fmt.has_audio:
                codecs += f" | A:{acodec_short}"

            table.add_row(
                str(idx),
                fmt.format_id,
                fmt.resolution,
                fmt.ext.upper(),
                fmt.filesize,
                codecs,
                fmt.quality_note
            )

        console.print(table)
        console.print()
        return display_formats

    def select_format(self, formats: List[VideoFormat]) -> Optional[str]:
        if not formats:
            return None

        console.print("[bold cyan]Format Selection Options:[/bold cyan]")
        console.print(f"  [green]1-{len(formats)}[/green] - Select specific format")
        console.print("  [green]b[/green]   - Best quality (default)")
        console.print("  [green]w[/green]   - Worst quality")
        console.print("  [green]bestaudio[/green] - Best audio only")
        console.print("  [green]q[/green]   - Quit")
        console.print()

        choice = prompt("Select format [b]: ", default="b").strip().lower()

        if choice == "q":
            return None
        elif choice in ("b", ""):
            return "best"
        elif choice == "w":
            return "worst"
        elif choice == "bestaudio":
            return "bestaudio"
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(formats):
                fmt = formats[idx]
                if fmt.has_video and fmt.has_audio:
                    return fmt.format_id
                elif fmt.has_video:
                    return f"{fmt.format_id}+bestaudio"
                else:
                    return fmt.format_id

        console.print("[yellow]Invalid choice, using best quality[/yellow]")
        return "best"

    def get_login_url(self, url: str) -> str:
        if "bilibili.com" in url or "b23.tv" in url:
            return "https://passport.bilibili.com/login"
        elif "youtube.com" in url or "youtu.be" in url:
            return "https://accounts.google.com/signin"
        elif "twitter.com" in url or "x.com" in url:
            return "https://twitter.com/login"
        elif "instagram.com" in url:
            return "https://www.instagram.com/accounts/login/"
        elif "facebook.com" in url:
            return "https://www.facebook.com/login"
        elif "nicovideo.jp" in url:
            return "https://account.nicovideo.jp/login"
        else:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc
            return f"https://{domain}"

    def open_login_page(self, url: str) -> bool:
        login_url = self.get_login_url(url)
        console.print(f"\n[cyan]Opening login page in browser...[/cyan]")
        console.print(f"[dim]{login_url}[/dim]")
        console.print()
        try:
            subprocess.run(["open", login_url], check=True)
            console.print("[yellow]Please log in to the website in your browser.[/yellow]")
            console.print("[yellow]After completing login, press Enter to continue...[/yellow]")
            input()
            return True
        except Exception as e:
            console.print(f"[red]Failed to open browser: {str(e)}[/red]")
            console.print(f"[yellow]Please manually open: {login_url}[/yellow]")
            console.print("[yellow]After completing login, press Enter to continue...[/yellow]")
            input()
            return True

    def needs_login_hint(self, url: str) -> bool:
        login_sites = [
            "bilibili.com", "b23.tv",
            "youtube.com", "youtu.be",
            "nicovideo.jp",
            "twitter.com", "x.com",
            "instagram.com",
            "facebook.com",
        ]
        url_lower = url.lower()
        return any(site in url_lower for site in login_sites)

    def ask_for_login(self, hint: str = "This site may require login for higher quality") -> Tuple[bool, List[str]]:
        console.print(Panel(
            f"[yellow]{hint}[/yellow]",
            title="🔐 Login Options",
            border_style="yellow"
        ))
        console.print()

        console.print("[bold cyan]Login Options:[/bold cyan]")
        console.print("  [green]1[/green] - Use browser cookies (Chrome) [default]")
        console.print("  [green]2[/green] - Use browser cookies (Safari)")
        console.print("  [green]3[/green] - Use browser cookies (Firefox)")
        console.print("  [green]4[/green] - Use cookies file")
        console.print("  [green]5[/green] - Username/Password login")
        console.print("  [green]q[/green] - Skip (download without login)")
        console.print()

        choice = prompt("Select option [1]: ", default="1").strip()

        if choice == "q":
            return False, []
        elif choice in ("1", ""):
            return True, ["--cookies-from-browser", "chrome"]
        elif choice == "2":
            return True, ["--cookies-from-browser", "safari"]
        elif choice == "3":
            return True, ["--cookies-from-browser", "firefox"]
        elif choice == "4":
            cookie_file = prompt("Enter cookies file path: ").strip()
            if os.path.exists(cookie_file):
                return True, ["--cookies", cookie_file]
            else:
                console.print("[red]File not found[/red]")
                return False, []
        elif choice == "5":
            username = prompt("Username: ").strip()
            password = prompt("Password: ", is_password=True).strip()
            return True, ["-u", username, "-p", password]
        else:
            return True, ["--cookies-from-browser", "chrome"]

    def download_video(self, url: str, format_id: str, extra_args: List[str]):
        output_path = "/Users/menggq/Downloads"
        
        console.print()
        console.print(Panel(
            f"Starting download...\nFormat: [cyan]{format_id}[/cyan]\nSave to: [yellow]{output_path}[/yellow]",
            title="⬇️  Download",
            border_style="green"
        ))
        console.print()

        console.print("[bold cyan]Download Method:[/bold cyan]")
        console.print("  [green]1[/green] - Multi-threaded (yt-dlp native, 16 threads) [default]")
        console.print("  [green]2[/green] - Multi-threaded (aria2c, 16 threads)")
        console.print("  [green]3[/green] - Single thread")
        console.print()

        choice = prompt("Select method [1]: ", default="1").strip()

        output_template = f"-o{output_path}/%(title)s.%(ext)s"

        base_cmd = ["yt-dlp", output_template, "--continue", "--part"]
        
        if choice in ("1", ""):
            base_cmd.extend(["-N", "16"])
            console.print("\n[cyan]Using yt-dlp native multi-thread (16 fragments)...[/cyan]")
        elif choice == "2":
            aria2c_args = [
                "--external-downloader", "aria2c",
                "--external-downloader-args", "-x 16 -s 16 -k 1M --continue=true --file-allocation=none"
            ]
            base_cmd.extend(aria2c_args)
            console.print("\n[cyan]Using aria2c with 16 threads...[/cyan]")
        else:
            console.print("\n[cyan]Using single thread...[/cyan]")

        if format_id == "best":
            cmd = base_cmd + extra_args + [url]
        elif format_id == "worst":
            cmd = base_cmd + ["-f", "worst"] + extra_args + [url]
        elif format_id == "bestaudio":
            cmd = base_cmd + ["-x", "--audio-format", "best"] + extra_args + [url]
        else:
            cmd = base_cmd + ["-f", format_id] + extra_args + [url]

        process = None
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            stdout = process.stdout
            if stdout:
                for line in stdout:
                    line = line.strip()
                    if line:
                        if "error" in line.lower():
                            console.print(f"[red]{line}[/red]")
                        elif "100%" in line or "complete" in line.lower():
                            console.print(f"[green]{line}[/green]")
                        else:
                            console.print(line)

            process.wait()

            if process.returncode == 0:
                temp_files = self.find_temp_files()
                if temp_files:
                    cleaned = self.clean_temp_files(temp_files)
                    console.print()
                    console.print(Panel(
                        f"✅ Download completed successfully!\n[dim]Cleaned {cleaned} temp files[/dim]",
                        border_style="green"
                    ))
                else:
                    console.print()
                    console.print(Panel(
                        "✅ Download completed successfully!",
                        border_style="green"
                    ))
            else:
                console.print()
                console.print(Panel(
                    f"❌ Download failed (code: {process.returncode})",
                    border_style="red"
                ))
                self.prompt_clean_on_cancel()

        except KeyboardInterrupt:
            console.print("\n[yellow]Download cancelled by user[/yellow]")
            if process:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
            self.prompt_clean_on_cancel()
        except Exception as e:
            console.print(f"\n[red]Error: {str(e)}[/red]")
            if process:
                process.terminate()
            self.prompt_clean_on_cancel()

    def run(self):
        temp_files = self.find_temp_files()
        if temp_files:
            clear()
            self.print_header()
            total_size = sum(os.path.getsize(f) for f in temp_files if os.path.exists(f))
            size_str = self.format_size(total_size)
            
            console.print(Panel(
                f"Found [yellow]{len(temp_files)}[/yellow] incomplete downloads ({size_str})\n\n"
                "You can resume these downloads by re-downloading the same videos,\n"
                "or clean them to free up disk space.",
                title="📥 Incomplete Downloads Found",
                border_style="yellow"
            ))
            console.print()
            
            console.print("[bold cyan]Options:[/bold cyan]")
            console.print("  [green]c[/green] - Clean all temp files")
            console.print("  [green]k[/green] - Keep for resume (continue)")
            console.print()
            
            choice = prompt("Select [k]: ", default="k").strip().lower()
            
            if choice == "c":
                cleaned = self.clean_temp_files(temp_files)
                console.print(f"[green]Cleaned {cleaned} temporary files.[/green]")
                console.print()
        
        try:
            while True:
                clear()
                self.url = self.get_url()

                if self.url.lower() in ("q", "quit", "exit"):
                    break
                
                if self.url.lower() == "clean":
                    temp_files = self.find_temp_files()
                    if temp_files:
                        total_size = sum(os.path.getsize(f) for f in temp_files if os.path.exists(f))
                        size_str = self.format_size(total_size)
                        console.print(f"\n[yellow]Found {len(temp_files)} temp files ({size_str})[/yellow]")
                        choice = prompt("Delete all? (y/n) [y]: ", default="y").strip().lower()
                        if choice == "y":
                            cleaned = self.clean_temp_files(temp_files)
                            console.print(f"[green]Cleaned {cleaned} files.[/green]")
                    else:
                        console.print("\n[green]No temp files found.[/green]")
                    input("\nPress Enter to continue...")
                    continue

                clear()
                self.print_header()

                extra_args: List[str] = []

                if self.is_playlist(self.url):
                    console.print("[cyan]Detected playlist URL![/cyan]")
                    console.print()
                    
                    if self.needs_login_hint(self.url):
                        use_login, extra_args = self.ask_for_login(
                            "Playlist may require login.\nSelect login method or skip."
                        )
                    
                    console.print("[cyan]Fetching playlist information...[/cyan]")
                    playlist = self.fetch_playlist_info(self.url, extra_args if extra_args else None)
                    
                    if playlist is None:
                        console.print("\n[red]Failed to fetch playlist info. Press Enter to try again.[/red]")
                        input()
                        continue
                    
                    self.fetch_video_titles_parallel(self.url, playlist, extra_args if extra_args else None)
                    
                    should_download, item_spec = self.select_playlist_items(playlist)
                    
                    if not should_download:
                        console.print("[yellow]Cancelled.[/yellow]")
                        continue
                    
                    console.print()
                    console.print("[bold cyan]Format Selection:[/bold cyan]")
                    console.print("  [green]b[/green]   - Best quality (default)")
                    console.print("  [green]w[/green]   - Worst quality")
                    console.print("  [green]bestaudio[/green] - Best audio only")
                    console.print("  [green]1080p[/green] - Max 1080p")
                    console.print("  [green]720p[/green]  - Max 720p")
                    console.print()
                    
                    format_choice = prompt("Select format [b]: ", default="b").strip().lower()
                    
                    if format_choice == "w":
                        format_id = "worst"
                    elif format_choice == "bestaudio":
                        format_id = "bestaudio"
                    elif format_choice == "1080p":
                        format_id = "best[height<=1080]"
                    elif format_choice == "720p":
                        format_id = "best[height<=720]"
                    else:
                        format_id = "best"
                    
                    self.download_playlist(self.url, playlist, extra_args, format_id, item_spec)
                    
                else:
                    if self.needs_login_hint(self.url):
                        console.print("[cyan]This site may require login for higher quality.[/cyan]")
                        use_login, extra_args = self.ask_for_login(
                            "Higher quality may require login.\nSelect login method or skip."
                        )
                        if use_login:
                            console.print("[cyan]Fetching with Chrome cookies...[/cyan]")
                            info = self.fetch_info(self.url, extra_args if extra_args else None)
                            
                            if info and info.requires_login:
                                console.print("\n[yellow]Chrome cookies not found or invalid.[/yellow]")
                                self.open_login_page(self.url)
                                console.print("[cyan]Retrying with Chrome cookies...[/cyan]")
                                info = self.fetch_info(self.url, extra_args)
                        else:
                            console.print("[cyan]Fetching video information...[/cyan]")
                            info = self.fetch_info(self.url)
                    else:
                        console.print("[cyan]Fetching video information...[/cyan]")
                        info = self.fetch_info(self.url)

                    if info is None:
                        console.print("\n[red]Failed to fetch video info. Press Enter to try again.[/red]")
                        input()
                        continue
                    
                    if info.is_playlist:
                        console.print()
                        console.print(Panel(
                            f"[cyan]Detected collection/playlist: {info.playlist_title}[/cyan]\n"
                            f"[yellow]{info.playlist_count} videos in this collection[/yellow]",
                            title="📋 Collection Detected",
                            border_style="cyan"
                        ))
                        console.print()
                        
                        console.print("[bold cyan]Options:[/bold cyan]")
                        console.print("  [green]d[/green] - Download entire collection")
                        console.print("  [green]s[/green] - Download single video only")
                        console.print()
                        
                        choice = prompt("Select [d]: ", default="d").strip().lower()
                        
                        if choice == "d":
                            console.print("[cyan]Fetching collection information...[/cyan]")
                            playlist = self.fetch_playlist_info(self.url, extra_args if extra_args else None)
                            
                            if playlist is None:
                                console.print("\n[red]Failed to fetch collection info. Press Enter to try again.[/red]")
                                input()
                                continue
                            
                            self.fetch_video_titles_parallel(self.url, playlist, extra_args if extra_args else None)
                            
                            should_download, item_spec = self.select_playlist_items(playlist)
                            
                            if not should_download:
                                console.print("[yellow]Cancelled.[/yellow]")
                                continue
                            
                            console.print()
                            console.print("[bold cyan]Format Selection:[/bold cyan]")
                            console.print("  [green]b[/green]   - Best quality (default)")
                            console.print("  [green]w[/green]   - Worst quality")
                            console.print("  [green]bestaudio[/green] - Best audio only")
                            console.print("  [green]1080p[/green] - Max 1080p")
                            console.print("  [green]720p[/green]  - Max 720p")
                            console.print()
                            
                            format_choice = prompt("Select format [b]: ", default="b").strip().lower()
                            
                            if format_choice == "w":
                                format_id = "worst"
                            elif format_choice == "bestaudio":
                                format_id = "bestaudio"
                            elif format_choice == "1080p":
                                format_id = "best[height<=1080]"
                            elif format_choice == "720p":
                                format_id = "best[height<=720]"
                            else:
                                format_id = "best"
                            
                            self.download_playlist(self.url, playlist, extra_args, format_id, item_spec)
                        else:
                            console.print("[cyan]Downloading single video only...[/cyan]")
                            console.print("[cyan]Fetching video formats...[/cyan]")
                            single_url = self.url
                            if "?" in single_url:
                                single_url = single_url.split("?")[0]
                            info = self.fetch_info(single_url + "?p=1", extra_args if extra_args else None)
                            
                            if info and not info.is_playlist:
                                self.display_video_info(info)
                                formats = self.display_formats(info)
                                format_id = self.select_format(formats)
                                if format_id:
                                    self.download_video(single_url + "?p=1", format_id, extra_args)
                            else:
                                console.print("[red]Failed to fetch single video info.[/red]")
                        continue

                    if info.requires_login:
                        self.display_video_info(info)
                        console.print("\n[yellow]Login required but cookies not working.[/yellow]")
                        self.open_login_page(self.url)
                        console.print("[cyan]Retrying with Chrome cookies...[/cyan]")
                        extra_args = ["--cookies-from-browser", "chrome"]
                        info = self.fetch_info(self.url, extra_args)
                        if info is None or info.requires_login:
                            console.print("\n[red]Still cannot access. Press Enter to try again.[/red]")
                            input()
                            continue

                    if info:
                        self.display_video_info(info)
                        formats = self.display_formats(info)
                        format_id = self.select_format(formats)

                        if format_id:
                            self.download_video(self.url, format_id, extra_args)

                console.print()
                cont = prompt("Download another video? (y/n) [y]: ", default="y").strip().lower()
                if cont != "y":
                    break

        except (KeyboardInterrupt, EOFError):
            console.print("\n\n[yellow]Goodbye! 👋[/yellow]")


def main():
    app = YtDlpTUI()
    app.run()


if __name__ == "__main__":
    main()
