import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';
import '../api.dart';
import '../theme.dart';

class ReelsScreen extends StatefulWidget {
  const ReelsScreen({super.key});
  @override
  State<ReelsScreen> createState() => _ReelsScreenState();
}

class _ReelsScreenState extends State<ReelsScreen> {
  List _pending = [];
  List _queue = [];
  bool _loading = true;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final results = await Future.wait([
        Api.instance.get('/api/instagram/reels/pending'),
        Api.instance.get('/api/instagram/reels/queue'),
      ]);
      _pending = (results[0] as List?) ?? [];
      _queue = (results[1] as List?) ?? [];
    } catch (_) {}
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _generate() async {
    setState(() => _busy = true);
    final res = await Api.instance.post('/api/instagram/reels/generate');
    _toast(res?['success'] == true ? 'Reel erzeugt' : 'Fehler: ${res?['error'] ?? 'unbekannt'}');
    setState(() => _busy = false);
    _load();
  }

  Future<void> _approve(String filename) async {
    await Api.instance.post('/api/instagram/reels/approve', {'filename': filename});
    _toast('Freigegeben');
    _load();
  }

  Future<void> _reject(String filename) async {
    await Api.instance.post('/api/instagram/reels/reject', {'filename': filename});
    _toast('Abgelehnt');
    _load();
  }

  Future<void> _postNow() async {
    setState(() => _busy = true);
    _toast('Wird gepostet...');
    final res = await Api.instance.post('/api/instagram/reels/post_now');
    _toast(res?['success'] == true ? 'Als Story gepostet!' : 'Fehler: ${res?['error'] ?? 'unbekannt'}');
    setState(() => _busy = false);
    _load();
  }

  void _toast(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg), backgroundColor: context.colors.surface2));
  }

  void _play(String filename) {
    launchUrl(Uri.parse(Api.instance.mediaUrl('/api/instagram/reels/media/$filename')), mode: LaunchMode.externalApplication);
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Scaffold(
      appBar: AppBar(title: const Text('Reels')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  Align(
                    alignment: Alignment.centerRight,
                    child: ElevatedButton.icon(
                      onPressed: _busy ? null : _generate,
                      icon: const Icon(Icons.movie_creation_outlined, size: 18),
                      label: const Text('Erzeugen'),
                      style: ElevatedButton.styleFrom(padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10)),
                    ),
                  ),
                  const SizedBox(height: 16),
                  Text('Zur Freigabe (${_pending.length})', style: TextStyle(color: c.text, fontSize: 16, fontWeight: FontWeight.w700)),
                  const SizedBox(height: 10),
                  if (_pending.isEmpty)
                    GlassCard(child: Text('Nichts zur Freigabe.', style: TextStyle(color: c.dim)))
                  else
                    ..._pending.map((f) => Padding(
                          padding: const EdgeInsets.only(bottom: 10),
                          child: GlassCard(
                            child: Row(children: [
                              IconButton(icon: const Icon(Icons.play_circle_fill, color: accent, size: 36), onPressed: () => _play(f)),
                              Expanded(child: Text(f, style: TextStyle(color: c.text, fontSize: 12), overflow: TextOverflow.ellipsis)),
                              IconButton(icon: const Icon(Icons.close, color: danger), onPressed: () => _reject(f)),
                              IconButton(icon: const Icon(Icons.check_circle, color: good), onPressed: () => _approve(f)),
                            ]),
                          ),
                        )),
                  const SizedBox(height: 20),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text('Warteschlange (${_queue.length})', style: TextStyle(color: c.text, fontSize: 16, fontWeight: FontWeight.w700)),
                      if (_queue.isNotEmpty)
                        TextButton.icon(
                          onPressed: _busy ? null : _postNow,
                          icon: const Icon(Icons.send, size: 16, color: accent),
                          label: const Text('Jetzt posten', style: TextStyle(color: accent)),
                        ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  if (_queue.isEmpty)
                    GlassCard(child: Text('Warteschlange leer.', style: TextStyle(color: c.dim)))
                  else
                    ..._queue.map((f) => Padding(
                          padding: const EdgeInsets.only(bottom: 10),
                          child: GlassCard(
                            child: Row(children: [
                              IconButton(icon: const Icon(Icons.play_circle_fill, color: good, size: 32), onPressed: () => _play(f)),
                              Expanded(child: Text(f, style: TextStyle(color: c.text, fontSize: 12), overflow: TextOverflow.ellipsis)),
                            ]),
                          ),
                        )),
                ],
              ),
      ),
    );
  }
}
