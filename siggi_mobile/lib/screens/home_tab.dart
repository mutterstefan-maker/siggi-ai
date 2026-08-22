import 'package:flutter/material.dart';
import '../api.dart';
import '../theme.dart';

class HomeTab extends StatefulWidget {
  final void Function(int tabIndex) onNavigate;
  const HomeTab({super.key, required this.onNavigate});
  @override
  State<HomeTab> createState() => _HomeTabState();
}

class _HomeTabState extends State<HomeTab> {
  Map<String, dynamic> _counts = {};
  List _pending = [];
  List _upcoming = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final results = await Future.wait([
        Api.instance.get('/api/counts'),
        Api.instance.get('/api/instagram/reels/pending'),
        Api.instance.get('/api/calendar/upcoming'),
      ]);
      _counts = (results[0] as Map?)?.cast<String, dynamic>() ?? {};
      _pending = (results[1] as List?) ?? [];
      _upcoming = (results[2] as List?) ?? [];
    } catch (_) {}
    if (mounted) setState(() => _loading = false);
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return RefreshIndicator(
      onRefresh: _load,
      child: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text('Übersicht', style: TextStyle(color: c.text, fontSize: 24, fontWeight: FontWeight.w800)),
                const SizedBox(height: 4),
                Text('Alles Wichtige auf einen Blick', style: TextStyle(color: c.dim, fontSize: 13)),
                const SizedBox(height: 20),
                GridView.count(
                  crossAxisCount: 2,
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  mainAxisSpacing: 12,
                  crossAxisSpacing: 12,
                  childAspectRatio: 1.3,
                  children: [
                    _statTile('📬', 'Posteingang', '${_counts['inbox'] ?? 0}', warn, () => widget.onNavigate(3)),
                    _statTile('📞', 'Callbacks', '${_counts['callbacks'] ?? 0}', accent, () => widget.onNavigate(3)),
                    _statTile('🎬', 'Reels-Freigabe', '${_pending.length}', good, () => widget.onNavigate(2)),
                    _statTile('💡', 'Verbesserungen', '${_counts['improvements'] ?? 0}', accent2, null),
                  ],
                ),
                const SizedBox(height: 20),
                if (_upcoming.isNotEmpty) ...[
                  Text('Nächster Termin', style: TextStyle(color: c.text, fontSize: 16, fontWeight: FontWeight.w700)),
                  const SizedBox(height: 10),
                  GlassCard(
                    onTap: () => widget.onNavigate(3),
                    child: Row(children: [
                      const Icon(Icons.event, color: accent),
                      const SizedBox(width: 12),
                      Expanded(child: Text(_upcoming.first['summary']?.toString() ?? 'Termin',
                          style: TextStyle(color: c.text, fontWeight: FontWeight.w600))),
                    ]),
                  ),
                  const SizedBox(height: 20),
                ],
                Text('Schnellzugriff', style: TextStyle(color: c.text, fontSize: 16, fontWeight: FontWeight.w700)),
                const SizedBox(height: 10),
                GlassCard(
                  onTap: () => widget.onNavigate(1),
                  child: Row(children: [
                    const Icon(Icons.auto_awesome, color: accent2),
                    const SizedBox(width: 12),
                    Expanded(child: Text('Mit Siggi chatten', style: TextStyle(color: c.text, fontWeight: FontWeight.w600))),
                    Icon(Icons.chevron_right, color: c.dim),
                  ]),
                ),
                const SizedBox(height: 10),
                GlassCard(
                  onTap: () => widget.onNavigate(2),
                  child: Row(children: [
                    const Icon(Icons.movie_creation_outlined, color: good),
                    const SizedBox(width: 12),
                    Expanded(child: Text('Reels & Bilder freigeben', style: TextStyle(color: c.text, fontWeight: FontWeight.w600))),
                    Icon(Icons.chevron_right, color: c.dim),
                  ]),
                ),
              ],
            ),
    );
  }

  Widget _statTile(String emoji, String label, String value, Color color, VoidCallback? onTap) {
    final c = context.colors;
    return GlassCard(
      onTap: onTap,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(emoji, style: const TextStyle(fontSize: 20)),
          Text(value, style: TextStyle(color: color, fontSize: 26, fontWeight: FontWeight.w800)),
          Text(label, style: TextStyle(color: c.dim, fontSize: 12)),
        ],
      ),
    );
  }
}
