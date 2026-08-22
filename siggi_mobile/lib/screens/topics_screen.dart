import 'package:flutter/material.dart';
import '../api.dart';
import '../theme.dart';

/// Custom topic ideas that flow into the flyer/reels image generation prompt
/// (instagram_flyer_engine.py prefers one of these when picking a topic) -
/// built so repeats can be steered by feeding in fresh ideas instead of
/// relying only on the fixed rotation.
class TopicsScreen extends StatefulWidget {
  const TopicsScreen({super.key});
  @override
  State<TopicsScreen> createState() => _TopicsScreenState();
}

class _TopicsScreenState extends State<TopicsScreen> {
  List _topics = [];
  bool _loading = true;
  final _newTopic = TextEditingController();
  bool _adding = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      _topics = (await Api.instance.get('/api/topics') as List?) ?? [];
    } catch (_) {}
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _add() async {
    final text = _newTopic.text.trim();
    if (text.isEmpty) return;
    setState(() => _adding = true);
    final res = await Api.instance.post('/api/topics', {'text': text});
    if (res?['success'] == true) _newTopic.clear();
    setState(() => _adding = false);
    _load();
  }

  Future<void> _delete(int id) async {
    await Api.instance.post('/api/topics/$id/delete');
    _load();
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Scaffold(
      appBar: AppBar(title: const Text('Themen-Ideen')),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(16),
            child: GlassCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Neue Idee', style: TextStyle(color: c.text, fontWeight: FontWeight.w700)),
                  const SizedBox(height: 4),
                  Text('Siggi bevorzugt diese Themen bei der nächsten Bild-/Reel-Erzeugung.', style: TextStyle(color: c.dim, fontSize: 11)),
                  const SizedBox(height: 10),
                  Row(children: [
                    Expanded(
                      child: TextField(
                        controller: _newTopic,
                        style: TextStyle(color: c.text),
                        onSubmitted: (_) => _add(),
                        decoration: const InputDecoration(hintText: 'z.B. Neue DSGVO-Pflichten für KMU'),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Container(
                      decoration: const BoxDecoration(gradient: accentGradient, shape: BoxShape.circle),
                      child: IconButton(
                        icon: _adding
                            ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                            : const Icon(Icons.add, color: Colors.white),
                        onPressed: _adding ? null : _add,
                      ),
                    ),
                  ]),
                ],
              ),
            ),
          ),
          Expanded(
            child: RefreshIndicator(
              onRefresh: _load,
              child: _loading
                  ? const Center(child: CircularProgressIndicator())
                  : _topics.isEmpty
                      ? Center(child: Text('Noch keine eigenen Themen-Ideen.', style: TextStyle(color: c.dim)))
                      : ListView.builder(
                          padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                          itemCount: _topics.length,
                          itemBuilder: (context, i) {
                            final t = _topics[i];
                            return Padding(
                              padding: const EdgeInsets.only(bottom: 8),
                              child: GlassCard(
                                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                                child: Row(children: [
                                  const Icon(Icons.lightbulb_outline, color: warn, size: 18),
                                  const SizedBox(width: 10),
                                  Expanded(child: Text(t['text']?.toString() ?? '', style: TextStyle(color: c.text, fontSize: 13))),
                                  IconButton(icon: Icon(Icons.close, color: c.dim, size: 18), onPressed: () => _delete(t['id'])),
                                ]),
                              ),
                            );
                          },
                        ),
            ),
          ),
        ],
      ),
    );
  }
}
